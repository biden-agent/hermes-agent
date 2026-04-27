# Feishu Patches

Hermes 飞书适配器增强补丁（基于 `NousResearch/hermes-agent`）。

## 改动文件

```
gateway/platforms/feishu.py           ← 核心改动
tests/gateway/test_feishu.py           ← require_mention 测试
tests/gateway/test_feishu_approval_buttons.py ← admin 审批测试
```

## 补丁内容

### 1. `FEISHU_REQUIRE_MENTION` — 可配置的 @mention 要求
- `FeishuAdapterSettings` 新增 `require_mention: bool = True`
- 读取环境变量 `FEISHU_REQUIRE_MENTION`（默认 true）
- `False` 时，群聊无需 @ 即可触发机器人

### 2. Admin-Only 审批
- `_handle_approval_card_action` 增加 admins 检查
- 非 admin 点审批 → 返回 "Only bot admins can approve this action."
- `FEISHU_ADMINS` 为空时向后兼容

### 3. `FEISHU_GROUP_POLICY=open`
- 全组织可对话，无需白名单

## 部署

```bash
# 环境变量（加到 ~/.hermes/.env）
FEISHU_GROUP_POLICY=open
FEISHU_REQUIRE_MENTION=false
FEISHU_ADMINS=ou_your_open_id

# 重启
hermes gateway restart
```

## 测试

```bash
venv/bin/python -m pytest tests/gateway/test_feishu.py tests/gateway/test_feishu_approval_buttons.py -v
# 212 passed
```

## 同步上游

```bash
git remote -v
# origin   https://github.com/NousResearch/hermes-agent.git
# private  https://github.com/biden-agent/hermes-agent.git

git fetch origin
git checkout main
git merge origin/main        # 同步上游
git checkout feishu-patches
git merge main               # 合并到补丁分支
git push private --all       # 推送
```
