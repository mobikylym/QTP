import subprocess
import sys
from pathlib import Path

import nox

sys.path.insert(0, str(Path(__file__).parent.resolve()))

BACKEND = Path('run.py')
FRONTEND = Path('src/app/frontend/app.py')

# mypy: disable-error-code=no-untyped-def

__test__ = False


@nox.session(python=False)
def lint(session):
    """
    Линтинг проекта (проверки соответствия определённым критериям) с автоматическим исправлением части ошибок.

    Запускается командой 'nox -s lint'.

    Проверяются следующие аспекты проекта:

    - Зависимости и установленные пакеты;
    - Локальное использование глобальных кред;
    - Сложные для понимания конструкции;
    - Потенциальные уязвимости;
    - Нежелательные используемые технологии;
    - Неиспользуемые импорты и переменные;
    - Неиспользуемый код;
    - Закомментированный код;
    - Наличие описания методов и классов;
    - Типизация параметров методов.
    """
    session.run('deptry', 'src')
    session.run('ruff', 'format', '.')
    session.run('ruff', 'check', '.', '--fix')
    session.run('mypy', '.')


@nox.session(python=False)
def backend(session):
    subprocess.Popen(
        ['uv', 'run', 'python', str(BACKEND)],
    )


@nox.session(python=False)
def frontend(session):
    subprocess.Popen(
        ['uv', 'run', 'streamlit', 'run', str(FRONTEND), '--server.port', '8501'],
    )


@nox.session(python=False)
def dev(session):
    subprocess.Popen(
        ['uv', 'run', 'python', str(BACKEND)],
    )
