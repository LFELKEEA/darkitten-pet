from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..model_context import ModelContext

from ..DEF import GOSA, PIVOT_TABLE_SIZE
from ..io.iserializable import ISerializable
from ..param import ParamPivots


class PivotManager(ISerializable):

    def __init__(self):
        self.paramPivotTable = None

    def read(self, br):
        self.paramPivotTable = br.readObject()

    def checkParamUpdated(self, modelContext):
        if modelContext.requireSetup():
            return True

        initVersion = modelContext.getInitVersion()
        for i in range(len(self.paramPivotTable) - 1, -1, -1):
            paramIndex = self.paramPivotTable[i].getParamIndex(initVersion)
            if paramIndex == ParamPivots.PARAM_INDEX_NOT_INIT:
                paramIndex = modelContext.getParamIndex(self.paramPivotTable[i].getParamID())

            if modelContext.isParamUpdated(paramIndex):
                return True

        return False

    def calcPivotValues(self, modelContext: 'ModelContext', ret: List[bool]):
        paramCount = len(self.paramPivotTable)
        initVersion = modelContext.getInitVersion()
        interpolationCount = 0
        for i in range(0, paramCount, 1):
            paramPivots = self.paramPivotTable[i]
            paramIndex = paramPivots.getParamIndex(initVersion)
            if paramIndex == ParamPivots.PARAM_INDEX_NOT_INIT:
                paramIndex = modelContext.getParamIndex(paramPivots.getParamID())
                paramPivots.setParamIndex(paramIndex, initVersion)

            if paramIndex < 0:
                raise Exception("err 23242 : " + paramPivots.getParamID())

            paramValue = 0 if paramIndex < 0 else modelContext.getParamFloat(paramIndex)
            pivotCount = paramPivots.getPivotCount()
            pivotValues = paramPivots.getPivotValues()
            pivotIndex = -1
            t = 0
            if pivotCount < 1:
                pass
            else:
                if pivotCount == 1:
                    pivotValue = pivotValues[0]
                    if pivotValue - GOSA < paramValue < pivotValue + GOSA:
                        pivotIndex = 0
                        t = 0
                    else:
                        pivotIndex = 0
                        ret[0] = True
                else:
                    pivotValue = pivotValues[0]
                    if paramValue < pivotValue - GOSA:
                        pivotIndex = 0
                        ret[0] = True
                    else:
                        if paramValue < pivotValue + GOSA:
                            pivotIndex = 0
                        else:
                            found = False
                            for j in range(1, pivotCount, 1):
                                nextPivotValue = pivotValues[j]
                                if paramValue < nextPivotValue + GOSA:
                                    if nextPivotValue - GOSA < paramValue:
                                        pivotIndex = j
                                    else:
                                        pivotIndex = j - 1
                                        t = (paramValue - pivotValue) / (nextPivotValue - pivotValue)
                                        interpolationCount += 1

                                    found = True
                                    break

                                pivotValue = nextPivotValue

                            if not found:
                                pivotIndex = pivotCount - 1
                                t = 0
                                ret[0] = True

            paramPivots.setTmpPivotIndex(pivotIndex)
            paramPivots.setTmpT(t)

        return interpolationCount

    def calcPivotIndices(self, indexArray, tArray, interpolationCount):
        tableSize = 1 << interpolationCount
        if tableSize + 1 > PIVOT_TABLE_SIZE:
            print("err 23245\n")

        paramCount = len(self.paramPivotTable)
        stride = 1
        divisor = 1
        tIndex = 0
        for i in range(0, tableSize, 1):
            indexArray[i] = 0

        for i in range(0, paramCount, 1):
            paramPivots = self.paramPivotTable[i]
            if paramPivots.getTmpT() == 0:
                offset = paramPivots.getTmpPivotIndex() * stride
                if offset < 0:
                    raise Exception("err 23246")

                for j in range(0, tableSize, 1):
                    indexArray[j] += offset
            else:
                offset1 = stride * paramPivots.getTmpPivotIndex()
                offset2 = stride * (paramPivots.getTmpPivotIndex() + 1)
                for j in range(0, tableSize, 1):
                    indexArray[j] += offset1 if (int(j / divisor) % 2 == 0) else offset2

                tArray[tIndex] = paramPivots.getTmpT()
                tIndex += 1
                divisor *= 2

            stride *= paramPivots.getPivotCount()

        indexArray[tableSize] = 65535
        tArray[tIndex] = -1

    def getParamCount(self):
        return len(self.paramPivotTable)
