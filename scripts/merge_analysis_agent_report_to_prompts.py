"""已废弃：报告规格改由 configs/analysis_agent_reports/*.json 直接加载，不再写入 prompts_bak_new.yaml。

保留本脚本仅作历史参考；运行将打印警告并退出。
"""
from __future__ import annotations

import sys


def main() -> None:
    print(
        "DEPRECATED: analysis_agent_report_* 已从 prompts_bak_new.yaml 移除。\n"
        "请直接维护 configs/analysis_agent_reports/{type}.v1.json",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
