"""Match "Booked by" (a name, initials, a nickname) to a person in the `people:` section."""

from __future__ import annotations

from ..config import Person


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


class People:
    def __init__(self, people: dict[str, Person]):
        self._by_name: dict[str, Person] = {}
        for person in people.values():
            for name in [person.name, *person.aliases]:
                if name.strip():
                    self._by_name[_norm(name)] = person

    def lookup(self, name: str | None) -> Person | None:
        return self._by_name.get(_norm(name)) if name else None
