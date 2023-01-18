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

import json
from copy import deepcopy
from functools import wraps
from typing import Callable, List, Union

import requests

from .._results_manager import wait
from .._results_manager.result import Result
from .._results_manager.results_manager import get_result
from .._shared_files.config import get_config
from .._workflow.lattice import Lattice
from .base import BaseDispatcher
from .utils.redispatch_helpers import redispatch_real


class LocalDispatcher(BaseDispatcher):
    """
    Local dispatcher which sends the workflow to the locally running
    dispatcher server.
    """

    @staticmethod
    def dispatch(
        orig_lattice: Lattice,
        dispatcher_addr: str = None,
    ) -> Callable:
        """
        Wrapping the dispatching functionality to allow input passing
        and server address specification.

        Afterwards, send the lattice to the dispatcher server and return
        the assigned dispatch id.

        Args:
            orig_lattice: The lattice/workflow to send to the dispatcher server.
            dispatcher_addr: The address of the dispatcher server.  If None then then defaults to the address set in Covalent's config.

        Returns:
            Wrapper function which takes the inputs of the workflow as arguments
        """

        if dispatcher_addr is None:
            dispatcher_addr = (
                get_config("dispatcher.address") + ":" + str(get_config("dispatcher.port"))
            )

        @wraps(orig_lattice)
        def wrapper(*args, **kwargs) -> str:
            """
            Send the lattice to the dispatcher server and return
            the assigned dispatch id.

            Args:
                *args: The inputs of the workflow.
                **kwargs: The keyword arguments of the workflow.

            Returns:
                The dispatch id of the workflow.
            """

            lattice = deepcopy(orig_lattice)

            lattice.build_graph(*args, **kwargs)

            # Serialize the transport graph to JSON
            json_lattice = lattice.serialize_to_json()

            # Extract triggers here
            json_lattice = json.loads(json_lattice)
            trigger_data = json_lattice["metadata"].pop("trigger")

            # Determine whether to disable first run
            disable_run = trigger_data is not None

            json_lattice = json.dumps(json_lattice)

            test_url = f"http://{dispatcher_addr}/api/submit"

            r = requests.post(test_url, data=json_lattice, params={"disable_run": disable_run})
            r.raise_for_status()

            lattice_dispatch_id = r.content.decode("utf-8").strip().replace('"', "")

            if not disable_run:
                return lattice_dispatch_id

            trigger_data["lattice_dispatch_id"] = lattice_dispatch_id
            LocalDispatcher.start_triggers(trigger_data)

            return lattice_dispatch_id

        return wrapper

    @staticmethod
    def dispatch_sync(
        lattice: Lattice,
        dispatcher_addr: str = None,
    ) -> Callable:
        """
        Wrapping the synchronous dispatching functionality to allow input
        passing and server address specification.

        Afterwards, sends the lattice to the dispatcher server and return
        the result of the executed workflow.

        Args:
            orig_lattice: The lattice/workflow to send to the dispatcher server.
            dispatcher_addr: The address of the dispatcher server. If None then then defaults to the address set in Covalent's config.

        Returns:
            Wrapper function which takes the inputs of the workflow as arguments
        """

        if dispatcher_addr is None:
            dispatcher_addr = (
                get_config("dispatcher.address") + ":" + str(get_config("dispatcher.port"))
            )

        @wraps(lattice)
        def wrapper(*args, **kwargs) -> Result:
            """
            Send the lattice to the dispatcher server and return
            the result of the executed workflow.

            Args:
                *args: The inputs of the workflow.
                **kwargs: The keyword arguments of the workflow.

            Returns:
                The result of the executed workflow.
            """

            return get_result(
                LocalDispatcher.dispatch(lattice, dispatcher_addr)(*args, **kwargs),
                wait=wait.EXTREME,
            )

        return wrapper

    @staticmethod
    def redispatch(
        dispatch_id,
        dispatcher_addr: str = None,
        replace_electrons={},
        reuse_previous_results=False,
    ):

        if dispatcher_addr is None:
            dispatcher_addr = (
                get_config("dispatcher.address") + ":" + str(get_config("dispatcher.port"))
            )

        def func(*new_args, **new_kwargs):
            body = redispatch_real(
                dispatch_id, new_args, new_kwargs, replace_electrons, reuse_previous_results
            )

            test_url = f"http://{dispatcher_addr}/api/redispatch"
            r = requests.post(test_url, json=body)
            r.raise_for_status()
            return r.content.decode("utf-8").strip().replace('"', "")

        return func

    @staticmethod
    def start_triggers(trigger_data, dispatcher_addr: str = None):

        if dispatcher_addr is None:
            dispatcher_addr = (
                get_config("dispatcher.address") + ":" + str(get_config("dispatcher.port"))
            )

        start_trigger_url = f"http://{dispatcher_addr}/api/triggers/start"

        r = requests.post(start_trigger_url, json=trigger_data)
        r.raise_for_status()

    @staticmethod
    def stop_triggers(dispatch_ids: Union[str, List[str]], dispatcher_addr: str = None):

        if dispatcher_addr is None:
            dispatcher_addr = (
                get_config("dispatcher.address") + ":" + str(get_config("dispatcher.port"))
            )

        if isinstance(dispatch_ids, str):
            dispatch_ids = [dispatch_ids]

        start_trigger_url = f"http://{dispatcher_addr}/api/triggers/stop"

        r = requests.post(start_trigger_url, json=dispatch_ids)
        r.raise_for_status()

        print("The following dispatch id's triggers should have stopped now:")

        for did in dispatch_ids:
            print(did)
