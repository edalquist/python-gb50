"""Constants and Enumerations for the Mitsubishi GB-50 / G-50 Central Controller."""

from enum import Enum


class DriveState(str, Enum):
    """HVAC unit operational power drive states."""
    OFF = "OFF"
    ON = "ON"
    TESTRUN = "TESTRUN"


class OperationMode(str, Enum):
    """HVAC unit operating modes."""
    FAN = "FAN"
    COOL = "COOL"
    HEAT = "HEAT"
    DRY = "DRY"
    AUTO = "AUTO"
    BAHP = "BAHP"
    AUTOCOOL = "AUTOCOOL"
    AUTOHEAT = "AUTOHEAT"
    VENTILATE = "VENTILATE"
    PANECOOL = "PANECOOL"
    PANEHEAT = "PANEHEAT"
    OUTCOOL = "OUTCOOL"
    DEFLOST = "DEFLOST"
    HEATRECOVERY = "HEATRECOVERY"
    BYPASS = "BYPASS"
    LC_AUTO = "LC_AUTO"
    HEATING = "HEATING"
    HEATING_ECO = "HEATING_ECO"
    HOT_WATER = "HOT_WATER"
    ANTI_FREEZE = "ANTI_FREEZE"
    COOLING = "COOLING"


class AirDirection(str, Enum):
    """Louver / vane air direction settings."""
    SWING = "SWING"
    VERTICAL = "VERTICAL"
    MID2 = "MID2"
    MID1 = "MID1"
    HORIZONTAL = "HORIZONTAL"
    MID0 = "MID0"
    AUTO = "AUTO"


class FanSpeed(str, Enum):
    """Fan blower speed stages."""
    LOW = "LOW"
    MID2 = "MID2"
    MID1 = "MID1"
    HIGH = "HIGH"
    AUTO = "AUTO"
    EXLOW = "EXLOW"


class RemoteControlPermission(str, Enum):
    """Local remote controller lock status."""
    PERMIT = "PERMIT"
    PROHIBIT = "PROHIBIT"


class SignStatus(str, Enum):
    """Filter sign and error indicator states."""
    OFF = "OFF"
    ON = "ON"
    RESET = "RESET"


class VentilationState(str, Enum):
    """Interlocked ventilation fan speeds."""
    OFF = "OFF"
    LOW = "LOW"
    HIGH = "HIGH"
    NONE = "NONE"


class ModelType(str, Enum):
    """M-Net equipment hardware model types."""
    IC = "IC"    # Indoor Unit (Air Conditioner / Fan Coil)
    LC = "LC"    # LOSSNAY Heat Recovery Energy Ventilator
    OC = "OC"    # Outdoor Condenser Unit
    BC = "BC"    # Branch Controller (R2 2-pipe heat recovery)
    IU = "IU"
    OS = "OS"
    TU = "TU"
    SC = "SC"    # Central / System Controller
    GW = "GW"    # Gateway
    TR = "TR"
    AN = "AN"
    KA = "KA"
    MA = "MA"
    IDC = "IDC"
    MC = "MC"    # Measurement Controller / Pulse Meter
    CDC = "CDC"
    VDC = "VDC"
    DDC = "DDC"
    RC = "RC"    # Remote Controller
    KIC = "KIC"
    AIC = "AIC"
    GR = "GR"
    OCi = "OCi"
    BS = "BS"
    ME = "ME"
    CR = "CR"
    SR = "SR"
    ST = "ST"
    DC = "DC"
    MCt = "MCt"
    MCp = "MCp"
    BU = "BU"
    WH = "WH"
    CE = "CE"
    HB = "HB"
    HS = "HS"
    NOUSE = "NOUSE"
    TMP = "TMP"
    UNKNOWN = "??"
    NONE = "NONE"


# Bulk byte index definitions
BULK_DRIVE_MAP = {
    0: DriveState.OFF,
    1: DriveState.ON,
    2: DriveState.TESTRUN,
    4: DriveState.ON,
    5: DriveState.ON,
    6: DriveState.OFF,
}

BULK_MODE_MAP = {
    0: OperationMode.FAN,
    1: OperationMode.COOL,
    2: OperationMode.HEAT,
    3: OperationMode.DRY,
    4: OperationMode.AUTO,
    5: OperationMode.BAHP,
    6: OperationMode.AUTOCOOL,
    7: OperationMode.AUTOHEAT,
    8: OperationMode.VENTILATE,
    9: OperationMode.PANECOOL,
    10: OperationMode.PANEHEAT,
    11: OperationMode.OUTCOOL,
    12: OperationMode.DEFLOST,
    128: OperationMode.HEATRECOVERY,
    129: OperationMode.BYPASS,
    130: OperationMode.LC_AUTO,
    144: OperationMode.HEATING,
    145: OperationMode.HEATING_ECO,
    146: OperationMode.HOT_WATER,
    147: OperationMode.ANTI_FREEZE,
    148: OperationMode.COOLING,
}

BULK_AIR_DIR_MAP = {
    0: AirDirection.SWING,
    1: AirDirection.VERTICAL,
    2: AirDirection.MID2,
    3: AirDirection.MID1,
    4: AirDirection.HORIZONTAL,
    5: AirDirection.MID0,
    6: AirDirection.AUTO,
}

BULK_FAN_SPEED_MAP = {
    0: FanSpeed.LOW,
    1: FanSpeed.MID2,
    2: FanSpeed.MID1,
    3: FanSpeed.HIGH,
    6: FanSpeed.AUTO,
    7: FanSpeed.EXLOW,
}

BULK_MODEL_MAP = {
    1: ModelType.UNKNOWN,
    2: ModelType.LC,
    3: ModelType.OC,
    4: ModelType.BC,
    5: ModelType.IU,
    6: ModelType.OS,
    7: ModelType.SC,
    8: ModelType.GW,
    9: ModelType.TR,
    10: ModelType.AN,
    11: ModelType.KA,
    12: ModelType.MA,
    13: ModelType.IDC,
    14: ModelType.MC,
    15: ModelType.CDC,
    16: ModelType.VDC,
    18: ModelType.TU,
    31: ModelType.IC,
    32: ModelType.DDC,
    33: ModelType.RC,
    34: ModelType.KIC,
    35: ModelType.AIC,
    36: ModelType.GR,
    37: ModelType.OCi,
    38: ModelType.BS,
    39: ModelType.SC,
    40: ModelType.IC,
    41: ModelType.ME,
    42: ModelType.CR,
    43: ModelType.SR,
    44: ModelType.ST,
    50: ModelType.DC,
    51: ModelType.MCt,
    52: ModelType.MCp,
    53: ModelType.BU,
    54: ModelType.WH,
    55: ModelType.CE,
    56: ModelType.HB,
    57: ModelType.HS,
    96: ModelType.NOUSE,
    97: ModelType.TMP,
    98: ModelType.UNKNOWN,
    99: ModelType.NONE,
}
