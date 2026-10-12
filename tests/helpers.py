from pathlib import Path


def settings(root, **changes):
    return {
        "profile": "test-profile", "bot_open_id": "ou_self", "bot_app_id": "cli_self",
        "data_dir": str(Path(root)/"state"), "lark_cli": "/fake/lark", "codex_bin": "/fake/codex",
        "groups": [{"chat_id": "oc_team", "project_dir": str(root), "start_ms": 1000}],
        **changes,
    }


def message(mid="om_one", **changes):
    return {
        "chat_id": "oc_team", "message_id": mid, "create_time": "2000", "update_time": "2000",
        "sender_id": "ou_other", "sender_type": "user", "message_type": "text",
        "content": "普通群消息", "mentions": [], **changes,
    }
