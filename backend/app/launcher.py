from __future__ import annotations

import json
import multiprocessing as mp
import signal
import socket
import threading
import time
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
from sqlalchemy import select

from .config import get_settings
from .database import init_database, session_scope
from .models import ModelConfig
from .seed import seed_defaults
from .worker import run_forever


def _wait_for_service_ready(
    port: int,
    *,
    parent_alive,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.1,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while parent_alive() and time.monotonic() < deadline:
        if _service_is_running(port):
            return True
        time.sleep(poll_seconds)
    return False


def _worker_entry(port: int | None = None) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    parent = mp.parent_process()
    parent_alive = lambda: parent is None or parent.is_alive()
    if port is not None and not _wait_for_service_ready(
        port,
        parent_alive=parent_alive,
    ):
        return
    run_forever(should_continue=parent_alive)


def _local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return str(probe.getsockname()[0])
    except OSError:
        return None


def _service_is_running(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("service") == "3d66-label-system"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


def _port_is_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main() -> None:
    mp.freeze_support()
    settings = get_settings()
    local_url = f"http://127.0.0.1:{settings.port}"
    if _service_is_running(settings.port):
        print("\n3d66 标签系统已经在运行，正在打开现有页面。\n")
        webbrowser.open(local_url)
        return
    if _port_is_in_use(settings.port):
        print(f"\n启动失败：端口 {settings.port} 已被其他程序占用。")
        print("请关闭占用该端口的程序，或联系管理员修改 APP_PORT。\n")
        raise SystemExit(1)
    init_database()
    with session_scope() as db:
        seed_defaults(db)
        model = db.scalar(
            select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id.asc())
        )
        worker_count = max(1, min(10, model.max_concurrency if model else 1))

    workers = [
        mp.Process(
            target=_worker_entry,
            args=(settings.port,),
            name=f"3d66-worker-{index + 1}",
            daemon=True,
        )
        for index in range(worker_count)
    ]
    for process in workers:
        process.start()

    print("\n3d66 标签系统已启动")
    print(f"当前电脑：{local_url}")
    lan_ip = _local_ip()
    if lan_ip:
        print(f"同一局域网：http://{lan_ip}:{settings.port}")
    print("关闭此窗口即可停止服务。首次联网访问时，请允许 Windows 防火墙放行专用网络。\n")

    browser_timer = threading.Timer(
        1.2,
        webbrowser.open,
        args=(local_url,),
    )
    browser_timer.daemon = True
    browser_timer.start()

    try:
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    finally:
        for process in workers:
            if process.is_alive():
                process.terminate()
        for process in workers:
            process.join(timeout=3)


if __name__ == "__main__":
    main()
