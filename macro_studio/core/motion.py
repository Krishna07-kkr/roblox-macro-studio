"""Motion that feels like Apple's: springs that carry their velocity and can be redirected.

A `Spring` chases a target the way UIKit/SwiftUI springs do (response and damping ratio), so a
change of mind mid-animation continues smoothly instead of jumping. Pure maths, no window.
"""

import math


class Spring:
    """A value that chases a target like a spring (Apple's response/damping-ratio form).

    `response` is roughly the seconds it takes to get most of the way; `damping` 1.0 settles
    without overshoot, below 1.0 it bounces. Changing the target mid-flight keeps the velocity,
    so reversing never jumps.
    """

    SUBSTEP = 1 / 120
    MAX_STEPS = 600

    def __init__(self, value, response=0.35, damping=1.0):
        if response <= 0:
            raise ValueError("response must be positive")
        self.value, self.velocity = float(value), 0.0
        self.response, self.damping = float(response), float(damping)

    def step(self, target, dt):
        omega = 2 * math.pi / self.response
        stiffness, drag = omega * omega, 2 * self.damping * omega
        steps = min(self.MAX_STEPS, max(1, math.ceil(dt / self.SUBSTEP)))
        sub = dt / steps
        for _ in range(steps):
            accel = stiffness * (target - self.value) - drag * self.velocity
            self.velocity += accel * sub
            self.value += self.velocity * sub
        return self.value

    def settled(self, target, eps=0.05):
        return abs(self.value - target) < eps and abs(self.velocity) < eps * 4

    def snap(self, value):
        self.value, self.velocity = float(value), 0.0
