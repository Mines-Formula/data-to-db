from dataclasses import dataclass
from typing import Optional


@dataclass
class ConversionProgress:
    name: str
    exception: Optional[Exception]

    def pop_exception(self) -> Exception:
        if self.exception is None:
            raise IndexError("No present excepetion")

        exception = self.exception
        self.exception = None

        return exception
