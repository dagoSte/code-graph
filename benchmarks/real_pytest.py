"""Host-only, offline pytest entry point for the frozen real-repository pilot."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ids', required=True)
    args = parser.parse_args()
    ids = json.loads(args.ids)
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
        parser.error('--ids must contain a nonempty JSON list')
    sys.path.insert(0, str(Path.cwd()))
    for key in list(os.environ):
        if key.startswith('PYTEST_'):
            del os.environ[key]
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'

    def offline(*args, **kwargs):
        raise RuntimeError('Network disabled for pilot tests')

    socket.socket.connect = socket.socket.connect_ex = offline
    socket.create_connection = socket.getaddrinfo = offline
    import pytest

    class Capture:
        def __init__(self):
            self.reports, self.collection_errors = [], 0

        def pytest_runtest_logreport(self, report):
            self.reports.append((report.when, report.outcome))

        def pytest_collectreport(self, report):
            self.collection_errors += int(report.failed)

    capture = Capture()
    started = time.monotonic()
    code = int(pytest.main(['-q', '--tb=short', '-p', 'no:cacheprovider', *ids], plugins=[capture]))
    rows = capture.reports
    result = dict(exit_code=code, seconds=round(time.monotonic() - started, 3),
                  executed=sum(when == 'call' for when, outcome in rows),
                  passed=sum(when == 'call' and outcome == 'passed' for when, outcome in rows),
                  failed=sum(when == 'call' and outcome == 'failed' for when, outcome in rows),
                  errors=capture.collection_errors + sum(when != 'call' and outcome == 'failed' for when, outcome in rows),
                  skipped=sum(outcome == 'skipped' for when, outcome in rows))
    print('CG_REAL_TEST=' + json.dumps(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
