from __future__ import annotations

import multiprocessing as mp
import signal
import socket

import uvicorn
from sqlalchemy import select

from .config import get_settings
from .database import init_database, session_scope
from .models import ModelConfig
from .seed import seed_defaults
from .worker import run_forever


def _worker_entry() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    run_forever()


def _local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return str(probe.getsockname()[0])
    except OSError:
        return None


def main() -> None:
    mp.freeze_support()
    settings = get_settings()
    init_database()
    with session_scope() as db:
        seed_defaults(db)
        model = db.scalar(
            select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id.asc())
        )
        worker_count = max(1, min(10, model.max_concurrency if model else 1))

    workers = [
        mp.Process(target=_worker_entry, name=f"3d66-worker-{index + 1}", daemon=True)
        for index in range(worker_count)
    ]
    for process in workers:
        process.start()

    print("\n3d66 标签系统已启动")
    print(f"当前电脑：http://127.0.0.1:{settings.port}")
    lan_ip = _local_ip()
    if lan_ip:
        print(f"同一局域网：http://{lan_ip}:{settings.port}")
    print("关闭此窗口即可停止服务。首次联网访问时，请允许 Windows 防火墙放行专用网络。\n")

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
