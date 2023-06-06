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

"""Routes for uploading and downloading workflow assets"""

import shutil
from typing import Tuple, Union

from fastapi import APIRouter, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from covalent._shared_files import logger

from .._dal.export import get_dispatch_asset_path, get_lattice_asset_path, get_node_asset_path
from .._dal.result import get_result_object
from .._db.datastore import workflow_db
from .._db.models import Lattice
from .models import (
    DispatchAssetKey,
    ElectronAssetKey,
    LatticeAssetKey,
    digest_pattern,
    digest_regex,
    range_pattern,
    range_regex,
)

app_log = logger.app_log
log_stack_info = logger.log_stack_info

router: APIRouter = APIRouter()

app_log = logger.app_log
log_stack_info = logger.log_stack_info

router: APIRouter = APIRouter()

_background_tasks = set()


@router.get("/{dispatch_id}/node/{node_id}/{key}")
def get_node_asset(
    dispatch_id: str,
    node_id: int,
    key: ElectronAssetKey,
    Range: Union[str, None] = Header(default=None, regex=range_regex),
):
    start_byte = 0
    end_byte = -1
    if Range:
        start_byte, end_byte = _extract_byte_range(Range)

    if end_byte >= 0 and end_byte < start_byte:
        raise HTTPException(
            status_code=400,
            detail="Invalid byte range",
        )
    app_log.debug(
        f"Requested asset {key.value} ([{start_byte}:{end_byte}]) for node {dispatch_id}:{node_id}"
    )

    with workflow_db.session() as session:
        lattice_record = session.query(Lattice).where(Lattice.dispatch_id == dispatch_id).first()
        status = lattice_record.status if lattice_record else None
        if not lattice_record:
            return JSONResponse(
                status_code=404,
                content={"message": f"The requested dispatch ID {dispatch_id} was not found."},
            )

    try:
        with workflow_db.session() as session:
            path = get_node_asset_path(session, dispatch_id, node_id, key.value)
    except:
        return JSONResponse(
            status_code=404,
            content={"message": f"Error retrieving asset {key.value}."},
        )
    generator = _generate_file_slice(path, start_byte, end_byte)
    return StreamingResponse(generator)


@router.get("/{dispatch_id}/dispatch/{key}")
def get_dispatch_asset(
    dispatch_id: str,
    key: DispatchAssetKey,
    Range: Union[str, None] = Header(default=None, regex=range_regex),
):
    start_byte = 0
    end_byte = -1
    if Range:
        start_byte, end_byte = _extract_byte_range(Range)

    if end_byte >= 0 and end_byte < start_byte:
        raise HTTPException(
            status_code=400,
            detail="Invalid byte range",
        )
    app_log.debug(
        f"Requested asset {key.value} ([{start_byte}:{end_byte}]) for dispatch {dispatch_id}"
    )

    with workflow_db.session() as session:
        lattice_record = session.query(Lattice).where(Lattice.dispatch_id == dispatch_id).first()
        status = lattice_record.status if lattice_record else None
        if not lattice_record:
            return JSONResponse(
                status_code=404,
                content={"message": f"The requested dispatch ID {dispatch_id} was not found."},
            )

    try:
        with workflow_db.session() as session:
            path = get_dispatch_asset_path(session, dispatch_id, key.value)
        app_log.debug(f"Dispatch result uri: {path}")
    except:
        return JSONResponse(
            status_code=404,
            content={"message": f"Error retrieving asset {key.value}."},
        )

    generator = _generate_file_slice(path, start_byte, end_byte)
    return StreamingResponse(generator)


@router.get("/{dispatch_id}/lattice/{key}")
def get_lattice_asset(
    dispatch_id: str,
    key: LatticeAssetKey,
    Range: Union[str, None] = Header(default=None, regex=range_regex),
):
    start_byte = 0
    end_byte = -1
    if Range:
        start_byte, end_byte = _extract_byte_range(Range)

    if end_byte >= 0 and end_byte < start_byte:
        raise HTTPException(
            status_code=400,
            detail="Invalid byte range",
        )
    app_log.debug(
        f"Requested lattice asset {key.value} ([{start_byte}:{end_byte}])for dispatch {dispatch_id}"
    )

    with workflow_db.session() as session:
        lattice_record = session.query(Lattice).where(Lattice.dispatch_id == dispatch_id).first()
        status = lattice_record.status if lattice_record else None
        if not lattice_record:
            return JSONResponse(
                status_code=404,
                content={"message": f"The requested dispatch ID {dispatch_id} was not found."},
            )

    try:
        with workflow_db.session() as session:
            path = get_lattice_asset_path(session, dispatch_id, key.value)
        app_log.debug(f"Lattice asset uri: {path}")
    except:
        return JSONResponse(
            status_code=404,
            content={"message": f"Error retrieving asset {key.value}."},
        )
    generator = _generate_file_slice(path, start_byte, end_byte)
    return StreamingResponse(generator)


@router.post("/{dispatch_id}/node/{node_id}/{key}")
def upload_node_asset(
    dispatch_id: str,
    node_id: int,
    key: str,
    asset_file: UploadFile,
    content_length: int = Header(),
    digest: Union[str, None] = Header(default=None, regex=digest_regex),
):
    app_log.debug(f"Requested asset {key} for node {dispatch_id}:{node_id}")

    with workflow_db.session() as session:
        lattice_record = session.query(Lattice).where(Lattice.dispatch_id == dispatch_id).first()
        status = lattice_record.status if lattice_record else None
        if not lattice_record:
            return JSONResponse(
                status_code=404,
                content={"message": f"The requested dispatch ID {dispatch_id} was not found."},
            )

    try:
        with workflow_db.session() as session:
            path = get_node_asset_path(session, dispatch_id, node_id, key)
    except:
        return JSONResponse(
            status_code=404,
            content={"message": f"Error retrieving metadata for asset {key}."},
        )

    # Update asset metadata
    with workflow_db.session() as session:
        update = {"size": content_length}
        if digest:
            alg, checksum = _extract_checksum(digest)
            update["digest_alg"] = alg
            update["digest"] = checksum
            res_obj = get_result_object(dispatch_id, bare=True, session=session)
            node = res_obj.lattice.transport_graph.get_node(node_id, session=session)
            node.update_assets(updates={key: update}, session=session)
        app_log.debug(f"Updated node asset {dispatch_id}:{node_id}:{key}")

    # Copy the tempfile to object store
    _copy_file_obj(asset_file.file, path)

    return f"Uploaded file to {path}"


@router.post("/{dispatch_id}/dispatch/{key}")
def upload_dispatch_asset(
    dispatch_id: str,
    key: DispatchAssetKey,
    asset_file: UploadFile,
    content_length: int = Header(),
    digest: Union[str, None] = Header(default=None, regex=digest_regex),
):
    with workflow_db.session() as session:
        lattice_record = session.query(Lattice).where(Lattice.dispatch_id == dispatch_id).first()
        status = lattice_record.status if lattice_record else None
        if not lattice_record:
            return JSONResponse(
                status_code=404,
                content={"message": f"The requested dispatch ID {dispatch_id} was not found."},
            )

    try:
        with workflow_db.session() as session:
            path = get_dispatch_asset_path(session, dispatch_id, key.value)
    except:
        return JSONResponse(
            status_code=404,
            content={"message": f"Error retrieving metadata for asset {key}."},
        )

    # Update asset metadata
    with workflow_db.session() as session:
        update = {"size": content_length}
        if digest:
            alg, checksum = _extract_checksum(digest)
            update["digest_alg"] = alg
            update["digest"] = checksum

            res_obj = get_result_object(dispatch_id, bare=True, session=session)
            res_obj.update_assets(updates={key: update}, session=session)
        app_log.debug(f"Updated size for dispatch asset {dispatch_id}:{key}")

    # Copy the tempfile to object store
    _copy_file_obj(asset_file.file, path)

    return f"Uploaded file to {path}"


@router.post("/{dispatch_id}/lattice/{key}")
def upload_lattice_asset(
    dispatch_id: str,
    key: LatticeAssetKey,
    asset_file: UploadFile,
    content_length: int = Header(),
    digest: Union[str, None] = Header(default=None, regex=digest_regex),
):
    with workflow_db.session() as session:
        lattice_record = session.query(Lattice).where(Lattice.dispatch_id == dispatch_id).first()
        status = lattice_record.status if lattice_record else None
        if not lattice_record:
            return JSONResponse(
                status_code=404,
                content={"message": f"The requested dispatch ID {dispatch_id} was not found."},
            )

    try:
        with workflow_db.session() as session:
            path = get_lattice_asset_path(session, dispatch_id, key.value)
    except:
        return JSONResponse(
            status_code=404,
            content={"message": f"Error retrieving metadata for asset {key}."},
        )

    # Update asset metadata
    with workflow_db.session() as session:
        update = {"size": content_length}
        if digest:
            alg, checksum = _extract_checksum(digest)
            update["digest_alg"] = alg
            update["digest"] = checksum

            res_obj = get_result_object(dispatch_id, bare=True, session=session)
            res_obj.lattice.update_assets(updates={key: update}, session=session)
        app_log.debug(f"Updated size for lattice asset {dispatch_id}:{key}")

    # Copy the tempfile to object store
    _copy_file_obj(asset_file.file, path)

    return f"Uploaded file to {path}"


def _copy_file_obj(src_fileobj, dest_path):
    with open(dest_path, "wb") as dest_fileobj:
        shutil.copyfileobj(src_fileobj, dest_fileobj)


def _generate_file_slice(file_path: str, start_byte: int, end_byte: int, chunk_size: int = 65536):
    """Generator of a byte slice from a file"""
    byte_pos = start_byte

    with open(file_path, "rb") as f:
        f.seek(start_byte)
        if end_byte < 0:
            for chunk in f:
                yield chunk
        else:
            while byte_pos + chunk_size < end_byte:
                byte_pos += chunk_size
                yield f.read(chunk_size)
            yield f.read(end_byte - byte_pos)


def _extract_byte_range(byte_range_header: str) -> Tuple[int, int]:
    start_byte = 0
    end_byte = -1
    match = range_pattern.match(byte_range_header)
    start = match.group(1)
    end = match.group(2)
    start_byte = int(start)
    if end:
        end_byte = int(end)

    return start_byte, end_byte


def _extract_checksum(digest_header: str) -> Tuple[str, str]:
    match = digest_pattern.match(digest_header)
    alg = match.group(0)
    checksum = match.group(1)
    return alg, checksum
