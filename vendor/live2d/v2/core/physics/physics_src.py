from .iphysics_param import IPhysicsParam
from .physics_constants import SRC_TO_Y, SRC_TO_G_ANGLE, SRC_TO_X


class PhysicsSrc(IPhysicsParam):

    def __init__(self, sourceType, paramId, scale, weight):
        super().__init__(paramId, scale, weight)
        self.sourceType = None
        self.sourceType = sourceType

    def update(self, model, physicsContext):
        scaledValue = self.scale * model.getParamFloat(self.paramId)
        physicsPoint1 = physicsContext.getPhysicsPoint1()

        if self.sourceType == SRC_TO_X:
            physicsPoint1.x = physicsPoint1.x + (scaledValue - physicsPoint1.x) * self.weight
        elif self.sourceType == SRC_TO_Y:
            physicsPoint1.y = physicsPoint1.y + (scaledValue - physicsPoint1.y) * self.weight
        elif self.sourceType == SRC_TO_G_ANGLE:
            angle = physicsContext.getAngle()
            angle = angle + (scaledValue - angle) * self.weight
            physicsContext.setAngle(angle)
