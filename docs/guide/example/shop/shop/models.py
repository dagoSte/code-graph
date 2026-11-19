"""Orders and their lines."""
from dataclasses import dataclass, field


@dataclass
class Line:
    sku: str
    quantity: int
    unit_price: int  # cents


@dataclass
class Order:
    id: str
    customer_id: str
    lines: list = field(default_factory=list)
    status: str = 'open'
    paid: int = 0  # cents captured from the customer

    def total(self):
        return sum(line.quantity * line.unit_price for line in self.lines)
