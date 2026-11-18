"""测试环境：独立 tmp DATA_DIR / FIXTURES_DIR（env 须先于 app 导入设置）。"""
import os
import shutil
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hoptrace-test-"))
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["FIXTURES_DIR"] = str(TMP / "fixtures")

FIXTURES_TMP = TMP / "fixtures"
FIXTURES_TMP.mkdir(parents=True, exist_ok=True)
REPO_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
for f in REPO_FIXTURES.glob("*.json"):
    shutil.copy(f, FIXTURES_TMP / f.name)

import pytest  # noqa: E402

from app import seed  # noqa: E402
from app.db import DB_PATH  # noqa: E402


@pytest.fixture()
def fresh_db():
    DB_PATH.unlink(missing_ok=True)
    seed.init_db()
    yield DB_PATH
