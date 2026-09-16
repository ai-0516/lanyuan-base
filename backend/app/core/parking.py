"""停车业务枚举。"""

from enum import Enum


class ParkingRentalStatus(str, Enum):
    """长期车位出租状态。"""

    ACTIVE = "active"
    INACTIVE = "inactive"
