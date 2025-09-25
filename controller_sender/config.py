from dataclasses import dataclass

@dataclass
class Settings:
    ip: str = "192.168.0.23"
    port: int = 4210
    rate: float = 30.0
    checksum: bool = True
    invert_y: bool = True
    controller_index: int = 0
