"""
开发/运维用：生成可写入 SERVICE_API_KEYS 的随机密钥字符串。

应用进程不在运行时通过 HTTP 签发密钥，仅读取环境变量并与请求头 Bearer 比对校验
（见 app/auth/dependencies.py）。部署、轮换与安全注意见 app/app-deploy/README.md 与
README-simple-deploy.md 中「Service API Key」小节；认证与安全总述见 ``docs/Service-API-Key-认证与安全说明.md``。

**本机生成一行密钥（仓库根目录执行，需能 import app）**

Linux / macOS::

    PYTHONPATH=. python -c "from app.auth.keygen import generate_service_api_key; print(generate_service_api_key())"

Windows PowerShell::

    $env:PYTHONPATH = (Get-Location).Path
    python -c "from app.auth.keygen import generate_service_api_key; print(generate_service_api_key())"

将输出写入 ``app/app-deploy/.env`` 的 ``SERVICE_API_KEYS``（或 CI/Secret 注入同名变量）。
多钥轮换：``SERVICE_API_KEYS=key1,key2``（英文逗号分隔，任一匹配即可）。
调用业务接口：``Authorization: Bearer <上述密钥之一>``。
"""

from __future__ import annotations

import secrets

# 默认熵长度：与常见「32 字节随机」建议一致（token_urlsafe 输出长度约为 ceil(nbytes*4/3)）
_DEFAULT_NBYTES = 32
_MIN_NBYTES = 16


def generate_service_api_key(*, nbytes: int = _DEFAULT_NBYTES) -> str:
    """
    使用操作系统 CSPRNG（secrets.token_urlsafe）生成 URL 安全随机串，适合作为 Service API Key。

    Args:
        nbytes: 底层随机字节数；越大越难暴力猜测，建议 >= 32。低于 16 会拒绝。

    Returns:
        可直接写入 SERVICE_API_KEYS（或逗号分隔列表中一项）的字符串。

    命令行一次性调用方式见本模块顶部的文档字符串。
    """
    if nbytes < _MIN_NBYTES:
        raise ValueError(f"nbytes must be >= {_MIN_NBYTES}, got {nbytes}")
    return secrets.token_urlsafe(nbytes)
