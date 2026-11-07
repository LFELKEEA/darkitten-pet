from abc import ABC, abstractmethod

from ..util import UtSystem
from ..util.ut_motion import UtMotion


class AMotion(ABC):

    def __init__(self):
        self.fadeInMSec = 1000
        self.fadeOutMSec = 1000
        self.weight = 1

    def setFadeIn(self, fade_in_ms):
        self.fadeInMSec = fade_in_ms

    def setFadeOut(self, fade_out_ms):
        self.fadeOutMSec = fade_out_ms

    def setWeight(self, weight):
        self.weight = weight

    def getFadeOut(self):
        return self.fadeOutMSec

    def getWeight(self):
        return self.weight

    def getDurationMSec(self):
        return -1

    def getLoopDurationMSec(self):
        return -1

    def updateParam(self, model, userTimeSeconds):
        if not userTimeSeconds.available or userTimeSeconds.finished:
            return

        currentTime = UtSystem.getUserTimeMSec()
        if userTimeSeconds.startTimeMSec < 0:
            userTimeSeconds.startTimeMSec = currentTime
            userTimeSeconds.fadeInStartTimeMSec = currentTime
            duration = self.getDurationMSec()
            if userTimeSeconds.endTimeMSec < 0:
                userTimeSeconds.endTimeMSec = -1 if (duration <= 0) else userTimeSeconds.startTimeMSec + duration

        weight = self.weight
        fadeIn = 1 if (self.fadeInMSec == 0) else UtMotion.getEasingSine(((currentTime - userTimeSeconds.fadeInStartTimeMSec) / self.fadeInMSec))
        fadeOut = 1 if (self.fadeOutMSec == 0 or userTimeSeconds.endTimeMSec < 0) else UtMotion.getEasingSine(((userTimeSeconds.endTimeMSec - currentTime) / self.fadeOutMSec))
        weight = weight * fadeIn * fadeOut
        if not (0 <= weight <= 1):
            print("### assert!! ### ")

        self.updateParamExe(model, currentTime, weight, userTimeSeconds)
        if 0 < userTimeSeconds.endTimeMSec < currentTime:
            userTimeSeconds.finished = True

    @abstractmethod
    def updateParamExe(self, model, currentTime, weight, userTimeSeconds):
        pass

    @staticmethod
    def getEasing(t, totalTime, accelerateTime):
        tNormalized = t / totalTime
        accelRatio = accelerateTime / totalTime
        u = accelRatio
        oneThird = 1 / 3
        twoThird = 2 / 3
        p0 = 1 - (1 - accelRatio) * (1 - accelRatio)
        p2 = 1 - (1 - u) * (1 - u)
        p3 = 0
        b0 = ((1 - accelRatio) * oneThird) * p0 + (u * twoThird + (1 - u) * oneThird) * (1 - p0)
        b1 = (u + (1 - u) * twoThird) * p2 + (accelRatio * oneThird + (1 - accelRatio) * twoThird) * (1 - p2)
        b2 = 1
        a = b2 - 3 * b1 + 3 * b0 - p3
        b = 3 * b1 - 6 * b0 + 3 * p3
        c = 3 * b0 - 3 * p3
        d = p3
        if tNormalized <= 0:
            return 0
        elif tNormalized >= 1:
            return 1

        time = tNormalized
        timeSquared = time * time
        timeCubed = time * timeSquared
        result = a * timeCubed + b * timeSquared + c * time + d
        return result
