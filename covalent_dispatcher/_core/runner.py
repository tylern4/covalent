# Copyright 2021 Agnostiq Inc.
#
# This file is part of Covalent.
#
# Licensed under the GNU Affero General Public License 3.0 (the "License").
# A copy of the License may be obtained with this software package or at
#
#      https://www.gnu.org/licenses/agpl-3.0.en.html
#
# Use of this file is prohibited except in compliance with the License. Any
# modifications or derivative works of this file must retain this copyright
# notice, and modified files must contain a notice indicating that they have
# been altered from the originals.
#
# Covalent is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the License for more details.
#
# Relief from the License may be granted by purchasing a commercial license.

"""
Defines the core functionality of the runner
"""

import traceback
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable, Dict, List, Tuple

from covalent._results_manager import Result
from covalent._shared_files import logger
from covalent._shared_files.config import get_config
from covalent._shared_files.context_managers import active_lattice_manager
from covalent._shared_files.defaults import prefix_separator, sublattice_prefix
from covalent._workflow import DepsBash, DepsCall, DepsPip
from covalent._workflow.lattice import Lattice
from covalent._workflow.transport import TransportableObject
from covalent.executor.base import wrapper_fn

from .._dal.result import Result as SRVResult
from . import data_manager as datasvc
from .runner_modules.utils import get_executor

app_log = logger.app_log
log_stack_info = logger.log_stack_info
debug_mode = get_config("sdk.log_level") == "debug"


# Domain: runner
# to be called by _run_abstract_task
async def _get_task_input_values(dispatch_id: str, abs_task_inputs: dict) -> dict:
    node_values = {}
    args = abs_task_inputs["args"]
    for node_id in args:
        value = await datasvc.get_electron_attribute(dispatch_id, node_id, "output")
        node_values[node_id] = value

    kwargs = abs_task_inputs["kwargs"]
    for key, node_id in kwargs.items():
        value = await datasvc.get_electron_attribute(dispatch_id, node_id, "output")
        node_values[node_id] = value

    return node_values


# Domain: runner
async def _run_abstract_task(
    dispatch_id: str,
    node_id: int,
    node_name: str,
    abstract_inputs: Dict,
    selected_executor: Any,
) -> None:

    # Resolve abstract task and inputs to their concrete (serialized) values
    timestamp = datetime.now(timezone.utc)

    try:
        serialized_callable = await datasvc.get_electron_attribute(
            dispatch_id, node_id, "function"
        )
        input_values = await _get_task_input_values(dispatch_id, abstract_inputs)

        abstract_args = abstract_inputs["args"]
        abstract_kwargs = abstract_inputs["kwargs"]
        args = [input_values[node_id] for node_id in abstract_args]
        kwargs = {k: input_values[v] for k, v in abstract_kwargs.items()}
        task_input = {"args": args, "kwargs": kwargs}

        app_log.debug(f"Collecting deps for task {node_id}")

        call_before, call_after = await _gather_deps(dispatch_id, node_id)

    except Exception as ex:
        app_log.error(f"Exception when trying to resolve inputs or deps: {ex}")
        node_result = datasvc.generate_node_result(
            node_id=node_id,
            start_time=timestamp,
            end_time=timestamp,
            status=Result.FAILED,
            error=str(ex),
        )
        return node_result

    node_result = datasvc.generate_node_result(
        node_id=node_id,
        start_time=timestamp,
        status=Result.RUNNING,
    )
    app_log.debug(f"7: Marking node {node_id} as running (_run_abstract_task)")

    await datasvc.update_node_result(dispatch_id, node_result)

    return await _run_task(
        dispatch_id=dispatch_id,
        node_id=node_id,
        serialized_callable=serialized_callable,
        selected_executor=selected_executor,
        node_name=node_name,
        call_before=call_before,
        call_after=call_after,
        inputs=task_input,
    )


# Domain: runner
async def _run_task(
    dispatch_id: str,
    node_id: int,
    inputs: Dict,
    serialized_callable: Any,
    selected_executor: Any,
    call_before: List,
    call_after: List,
    node_name: str,
) -> None:
    """
    Run a task with given inputs on the selected executor.
    Also updates the status of current node execution while
    checking if a redispatch has occurred. Exclude those nodes
    from execution which were completed.

    Also verifies if execution of this dispatch has been cancelled.

    Args:
        inputs: Inputs for the task.
        result_object: Result object being used for current dispatch
        node_id: Node id of the task to be executed.

    Returns:
        None
    """

    dispatch_info = await datasvc.get_dispatch_attributes(dispatch_id, ["results_dir"])
    results_dir = dispatch_info["results_dir"]

    # Instantiate the executor from JSON
    try:
        app_log.debug(f"Instantiating executor for {dispatch_id}:{node_id}")
        executor = get_executor(node_id, selected_executor)
    except Exception as ex:
        tb = "".join(traceback.TracebackException.from_exception(ex).format())
        app_log.debug("Exception when trying to instantiate executor:")
        app_log.debug(tb)
        error_msg = tb if debug_mode else str(ex)
        node_result = datasvc.generate_node_result(
            node_id=node_id,
            end_time=datetime.now(timezone.utc),
            status=Result.FAILED,
            error=error_msg,
        )
        return node_result

    # run the task on the executor and register any failures
    try:
        app_log.debug(f"Executing task {node_name}")
        assembled_callable = partial(wrapper_fn, serialized_callable, call_before, call_after)
        execute_callable = partial(
            executor.execute,
            function=assembled_callable,
            args=inputs["args"],
            kwargs=inputs["kwargs"],
            dispatch_id=dispatch_id,
            results_dir=results_dir,
            node_id=node_id,
        )
        output, stdout, stderr, exception_raised = await executor._execute(
            function=assembled_callable,
            args=inputs["args"],
            kwargs=inputs["kwargs"],
            dispatch_id=dispatch_id,
            results_dir=results_dir,
            node_id=node_id,
        )
        if exception_raised:
            status = Result.FAILED
        else:
            status = Result.COMPLETED

        node_result = datasvc.generate_node_result(
            node_id=node_id,
            end_time=datetime.now(timezone.utc),
            status=status,
            output=output,
            stdout=stdout,
            stderr=stderr,
        )

    except Exception as ex:
        tb = "".join(traceback.TracebackException.from_exception(ex).format())
        app_log.debug(f"Exception occurred when running task {node_id}:")
        app_log.debug(tb)
        error_msg = tb if debug_mode else str(ex)
        node_result = datasvc.generate_node_result(
            node_id=node_id,
            end_time=datetime.now(timezone.utc),
            status=Result.FAILED,
            error=error_msg,
        )
    app_log.debug(f"Node result: {node_result}")
    return node_result


# Domain: runner
async def _gather_deps(dispatch_id: str, node_id: int) -> Tuple[List, List]:
    """Assemble deps for a node into the final call_before and call_after"""

    deps = await datasvc.get_electron_attribute(dispatch_id, node_id, "deps")

    # Assemble call_before and call_after from all the deps

    call_before_objs_json = await datasvc.get_electron_attribute(
        dispatch_id, node_id, "call_before"
    )

    call_after_objs_json = await datasvc.get_electron_attribute(dispatch_id, node_id, "call_after")

    call_before = []
    call_after = []

    # Rehydrate deps from JSON
    if "bash" in deps:
        dep = DepsBash()
        dep.from_dict(deps["bash"])
        call_before.append(dep.apply())

    if "pip" in deps:
        dep = DepsPip()
        dep.from_dict(deps["pip"])
        call_before.append(dep.apply())

    for dep_json in call_before_objs_json:
        dep = DepsCall()
        dep.from_dict(dep_json)
        call_before.append(dep.apply())

    for dep_json in call_after_objs_json:
        dep = DepsCall()
        dep.from_dict(dep_json)
        call_after.append(dep.apply())

    return call_before, call_after


# Domain: runner
async def run_abstract_task(
    dispatch_id: str,
    node_id: int,
    node_name: str,
    abstract_inputs: Dict,
    selected_executor: Any,
) -> None:

    node_result = await _run_abstract_task(
        dispatch_id=dispatch_id,
        node_id=node_id,
        node_name=node_name,
        abstract_inputs=abstract_inputs,
        selected_executor=selected_executor,
    )
    await datasvc.update_node_result(dispatch_id, node_result)


# Domain: runner
# This is to be run out-of-process
def _post_process(workflow_function: Callable, inputs: Dict, node_outputs: Dict) -> Any:
    """
    Post processing function to be called after the lattice execution.
    This takes care of executing statements that were not an electron
    but were inside the lattice's function. It also replaces any calls
    to an electron with the result of that electron execution, hence
    preventing a local execution of electron's function.

    Note: Here `node_outputs` is used instead of `electron_outputs`
    since an electron can be called multiple times with possibly different
    arguments, but every time it's called, it will be executed as a separate node.
    Thus, output of every node is used.

    Args:
        lattice: Lattice object that was dispatched.
        node_outputs: Dictionary containing the output of all the nodes.
        execution_order: List of lists containing the order of execution of the nodes.

    Reurns:
        result: The result of the lattice function.
    """

    lattice = Lattice(workflow_function)
    lattice.args = inputs["args"]
    lattice.kwargs = inputs["kwargs"]

    ordered_node_outputs = []
    app_log.debug(f"node_outputs: {node_outputs}")
    app_log.debug(f"node_outputs: {node_outputs.items()}")
    for i, item in enumerate(node_outputs.items()):
        key, val = item
        app_log.debug(f"Here's the key: {key}")
        if not key.startswith(prefix_separator) or key.startswith(sublattice_prefix):
            ordered_node_outputs.append(val)

    with active_lattice_manager.claim(lattice):
        lattice.post_processing = True
        lattice.electron_outputs = ordered_node_outputs
        args = [arg.get_deserialized() for arg in lattice.args]
        kwargs = {k: v.get_deserialized() for k, v in lattice.kwargs.items()}
        workflow_function = lattice.workflow_function.get_deserialized()
        result = workflow_function(*args, **kwargs)
        lattice.post_processing = False
        return result


# Domain: runner
async def _postprocess_workflow(result_object: SRVResult) -> SRVResult:
    """
    Postprocesses a workflow with a completed computational graph

    Args:
        result_object: Result object being used for current dispatch

    Returns:
        The postprocessed result object
    """

    # Executor for post_processing
    dispatch_id = result_object.dispatch_id
    pp_executor = result_object.lattice.get_value("workflow_executor")
    pp_executor_data = result_object.lattice.get_value("workflow_executor_data")
    post_processor = [pp_executor, pp_executor_data]

    result_object._status = Result.POSTPROCESSING
    result_object.commit()

    app_log.debug(f"Preparing to post-process workflow {result_object.dispatch_id}")

    if pp_executor == "client":
        app_log.debug("Workflow to be postprocessed client side")
        dispatch_result = datasvc.generate_dispatch_result(
            dispatch_id,
            status=Result.PENDING_POSTPROCESSING,
            end_time=datetime.now(timezone.utc),
        )
        await datasvc.update_dispatch_result(dispatch_id, dispatch_result)
        return result_object

    post_processing_inputs = {}
    serialized_workflow = result_object.lattice.get_value("workflow_function")

    post_processing_inputs["args"] = [
        serialized_workflow,
        TransportableObject(result_object.get_value("inputs")),
        TransportableObject((result_object.get_all_node_outputs())),
    ]
    post_processing_inputs["kwargs"] = {}

    app_log.debug(f"Submitted post-processing job to executor {post_processor}")
    post_process_result = await _run_task(
        dispatch_id=result_object.dispatch_id,
        node_id=-1,
        serialized_callable=TransportableObject(_post_process),
        selected_executor=post_processor,
        node_name="post_process",
        call_before=[],
        call_after=[],
        inputs=post_processing_inputs,
    )

    if post_process_result["status"] != Result.COMPLETED:
        stderr = post_process_result["stderr"] if post_process_result["stderr"] else ""
        err = post_process_result["error"] if post_process_result["error"] else ""
        error_msg = stderr + err

        app_log.debug(f"Post-processing failed: {err}")
        dispatch_result = datasvc.generate_dispatch_result(
            dispatch_id,
            status=Result.POSTPROCESSING_FAILED,
            error=f"Post-processing failed: {error_msg}",
            end_time=datetime.now(timezone.utc),
        )
        await datasvc.update_dispatch_result(dispatch_id, dispatch_result)
        app_log.debug("Returning from _postprocess_workflow")
        return result_object

    result_object._result = post_process_result["output"]
    result_object._status = Result.COMPLETED
    result_object._end_time = datetime.now(timezone.utc)

    app_log.debug(
        f"10: Successfully post-processed result {result_object.dispatch_id} (run_planned_workflow)"
    )

    return result_object


async def postprocess_workflow(dispatch_id: str) -> SRVResult:
    sdkres = datasvc.get_result_object(dispatch_id)
    return await _postprocess_workflow(sdkres)
