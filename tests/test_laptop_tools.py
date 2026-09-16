import base64
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from laptop_tools import CommandJobs, computer_capabilities, file_transfer


@pytest.fixture
def jobs(tmp_path):
    manager = CommandJobs(tmp_path / "jobs with spaces")
    yield manager
    manager.close()


def assert_process_exits(process, timeout=5):
    try:
        process.wait(timeout=timeout)
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired:
        pytest.fail(f"Process {process.pid} did not exit within {timeout} seconds")


def test_native_argv_cwd_environment_and_exit_status(jobs, tmp_path):
    unusual = 'spaces, "quotes", $HOME, backtick` and trailing\\'
    result = jobs.start(
        program=sys.executable,
        arguments=[
            "-c",
            "import os,sys; print(sys.argv[1]); print(os.getcwd()); print(os.environ['MCP_TEST_VALUE']); print('stderr!',file=sys.stderr); sys.exit(9)",
            unusual,
        ],
        cwd=str(tmp_path),
        env={"MCP_TEST_VALUE": "value"},
        wait_seconds=10,
    )
    assert result["exit_code"] == 9
    assert unusual in result["stdout"]["text"]
    assert str(tmp_path) in result["stdout"]["text"]
    assert "value" in result["stdout"]["text"]
    assert "stderr!" in result["stderr"]["text"]


def test_large_output_is_retrievable_without_truncation(jobs):
    result = jobs.start(
        program=sys.executable,
        arguments=[
            "-c",
            "import sys; sys.stdout.write('a'*200000+'😀'*30); sys.stderr.write('b'*90000)",
        ],
        wait_seconds=10,
    )
    pieces = []
    offset = 0
    while True:
        part = jobs.read(result["job_id"], stdout_offset=offset, max_bytes=999)["stdout"]
        pieces.append(part["text"])
        offset = part["next_offset"]
        if not part["has_more"]:
            break
    assert "".join(pieces) == "a" * 200000 + "😀" * 30
    assert result["stderr"]["has_more"]


def test_stdin_and_close(jobs):
    result = jobs.start(
        program=sys.executable,
        arguments=["-c", "import sys; print(sys.stdin.read())"],
        wait_seconds=0,
    )
    jobs.write(result["job_id"], "hello unicode é 😀\n", close_stdin=True)
    result = jobs.read(result["job_id"], wait_seconds=10)
    assert result["exit_code"] == 0
    assert "hello unicode é 😀" in result["stdout"]["text"]
    with pytest.raises(ValueError, match="closed"):
        jobs.write(result["job_id"], "too late")


def test_timeout_is_explicit_and_terminates(jobs):
    result = jobs.start(
        program=sys.executable,
        arguments=["-c", "import time; time.sleep(30)"],
        timeout_seconds=0.2,
        wait_seconds=2,
    )
    assert result["timed_out"] is True
    assert result["exit_code"] is not None


def test_terminate_kills_child_process(jobs):
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    result = jobs.start(program=sys.executable, arguments=["-u", "-c", code], wait_seconds=1)
    child_pid = int(result["stdout"]["text"].strip())
    child = psutil.Process(child_pid)
    result = jobs.terminate(result["job_id"])
    assert result["exit_code"] is not None
    assert_process_exits(child)


def test_powershell_script_longer_than_windows_command_line_limit(jobs):
    script = (
        "# "
        + "x" * 40000
        + "\nWrite-Output 'long-script-ok'\n[Console]::Error.WriteLine('err-too')\nexit 4"
    )
    result = jobs.start(command=script, wait_seconds=10)
    assert result["exit_code"] == 4
    assert "long-script-ok" in result["stdout"]["text"]
    assert "err-too" in result["stderr"]["text"]


def test_children_remain_owned_after_parent_exits(jobs):
    code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True)"
    result = jobs.start(program=sys.executable, arguments=["-u", "-c", code], wait_seconds=1)
    child = psutil.Process(int(result["stdout"]["text"].strip()))
    assert result["exit_code"] == 0
    assert result["state"] == "running"
    jobs.terminate(result["job_id"])
    assert_process_exits(child)


def test_server_crash_closes_job_and_kills_children(tmp_path):
    module_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    code = f"""
import os,sys
from pathlib import Path
sys.path.insert(0, {module_dir!r})
from laptop_tools import CommandJobs
manager = CommandJobs(Path({str(tmp_path)!r}) / 'crash-jobs')
job = manager.start(program=sys.executable, arguments=['-c', 'import time; time.sleep(60)'], wait_seconds=0)
print(job['pid'], flush=True)
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    pid = int(result.stdout.strip())
    deadline = time.monotonic() + 3
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(pid)


def test_powershell_nonterminating_errors_become_failures(jobs):
    result = jobs.start(
        command="Get-Item -LiteralPath 'Z:\\mcp-no-such-fixture-4629094'", wait_seconds=10
    )
    assert result["exit_code"] != 0
    assert result["stderr"]["text"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"command": "echo x", "program": sys.executable},
        {"command": "echo x", "timeout_seconds": -1},
        {"command": "echo x", "wait_seconds": 31},
        {"command": "echo x", "max_bytes": 3},
        {"command": "echo x", "cwd": "relative"},
    ],
)
def test_invalid_job_requests_do_not_start(jobs, kwargs):
    with pytest.raises(ValueError):
        jobs.start(**kwargs)
    assert not jobs.list()["jobs"]


def test_binary_round_trip_and_overwrite_protection(tmp_path):
    path = str(tmp_path / "binary ü file.bin")
    payload = bytes(range(256)) * 300
    file_transfer("write", path, data_base64=base64.b64encode(payload[:400]).decode())
    file_transfer("write", path, offset=400, data_base64=base64.b64encode(payload[400:]).decode())
    result = file_transfer("read", path, length=100000)
    assert base64.b64decode(result["data_base64"]) == payload and result["eof"]
    with pytest.raises(FileExistsError):
        file_transfer("write", path, data_base64="")
    assert Path(path).read_bytes() == payload
    file_transfer("write", path, data_base64="YQ==", overwrite=True)
    assert Path(path).read_bytes() == b"a"


def test_invalid_base64_cannot_truncate_file(tmp_path):
    target = tmp_path / "existing.bin"
    target.write_bytes(b"keep")
    with pytest.raises(ValueError):
        file_transfer("write", str(target), data_base64="%%invalid%%", overwrite=True)
    assert target.read_bytes() == b"keep"


def test_transfer_offsets_and_paths_are_unambiguous(tmp_path):
    target = tmp_path / "test.bin"
    target.write_bytes(b"a")
    for arguments in ({"offset": -1}, {"offset": 2}, {"length": 0}, {"length": 2**24}):
        with pytest.raises(ValueError):
            file_transfer("read", str(target), **arguments)
    with pytest.raises(ValueError):
        file_transfer("read", "relative.txt")
    with pytest.raises(ValueError):
        file_transfer("write", str(target), offset=2, data_base64="Yg==")
    assert target.read_bytes() == b"a"


def test_actual_capabilities_are_facts():
    result = computer_capabilities()
    assert isinstance(result["elevated"], bool)
    assert result["python_executable"] == sys.executable
    assert result["command_allowlist"] is None
    assert result["filesystem_sandbox"] is None
    assert result["versions"]["windows-mcp"] == "0.8.5"
