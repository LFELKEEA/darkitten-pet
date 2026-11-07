from .physics_constants import TARGET_FROM_ANGLE, TARGET_FROM_ANGLE_V
from .iphysics_param import IPhysicsParam


class PhysicsTarget(IPhysicsParam):

    def __init__(self, targetType, paramId, scale, weight):
        super().__init__(paramId, scale, weight)
        self.targetType = targetType

    def update(self, model, physicsContext):
        if self.targetType == TARGET_FROM_ANGLE:
            model.setParamFloat(self.paramId, self.scale * physicsContext.getLastAngle(), self.weight)
        elif self.targetType == TARGET_FROM_ANGLE_V:
            model.setParamFloat(self.paramId, self.scale * physicsContext.getLastAngleVelocity(), self.weight)
