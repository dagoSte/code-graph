import functools


@functools.lru_cache(maxsize=None)
def cached_unused():
    return 1


def plain_unused():
    return 2


class Shape:
    @staticmethod
    def static_unused():
        return 3

    @property
    def area(self):
        return 4
