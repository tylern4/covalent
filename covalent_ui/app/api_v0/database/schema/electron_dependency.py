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

"""Electron dependency schema"""

import enum

from sqlalchemy import BigInteger, Column, DateTime, Enum, Integer, Text

from covalent_ui.app.api_v0.database.config.db import Base


class ParameterTypeEnum(enum.Enum):
    """Parameter Type Of Enum

    Attributes:
        arg: Arguments
        kwarg: keywords
        null: null value
    """

    ARG = 1
    KWARG = 2
    NULL = 3


class ElectronDependency(Base):
    """Electron Dependency

    Attributes:
        id: primary key id
        electron_id: unique electron id
        parent_electron_id: parent electron id
        edge_name: edge name for electron
        parameter_type: parameter type of enum
        arg_index: Argument Posistion
        created_at: created date
    """

    __tablename__ = "electron_dependency"
    id = Column(BigInteger, primary_key=True)

    electron_id = Column(Integer, nullable=False)

    parent_electron_id = Column(Integer, nullable=False)

    edge_name = Column(Text, nullable=False)

    parameter_type = Column(Enum(ParameterTypeEnum), nullable=False)

    arg_index = Column(Integer, nullable=False)

    created_at = Column(DateTime, nullable=False)
