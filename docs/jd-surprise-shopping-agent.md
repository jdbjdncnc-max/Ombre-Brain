# 京东购物 MCP：架构与安全边界

## 当前实现

```text
Ombre 普通 MCP 管理器
  -> Zeabur 上的 /api/jd-shopping/mcp
  -> search_surprise_gift
  -> Zeabur 持久任务队列
  -> 用户电脑上的常驻 Edge 执行器
  -> submit_authorized_jd_order
  -> 本机额度核验、单 SKU 结算、提交订单、尝试余额支付
```

京东购物不再加入 Ombre 的内置隐藏工具目录，也没有购物专属系统提示词。它与论坛、游戏等外部工具一样，由工具管理页导入、发现、勾选和停用。

设为“停用”时，工具定义不会进入对话请求。设为“仅对话可用”并勾选工具时，两个标准 MCP 工具会通过模型原生工具接口提供给对话模型。

## 为什么仍需要本机执行器

京东 Cookie、登录与支付页面全部留在用户的常用电脑。Zeabur 只保存短期任务和结果，不能直接操作 Windows 上已经登录的 Edge。因此购物时电脑仍需在线并运行本地 worker。

如果以后把 Ombre 后端和浏览器都迁移到同一台 Mac mini，可改用 `packages/jd-shopping-agent/src/mcp-server.mjs` 提供的标准 `stdio` MCP，搜索和下单逻辑无需重写。

## 授权边界

- 本地签名文件记录单笔上限、每月总额、每月订单数、截止日期、是否自动提交、是否尝试京东余额。
- 云端 `OMBRE_JD_MAX_BUDGET_CNY` 只能进一步收紧上限，不能放宽本地授权。
- 每单固定一件、一个 SKU，并且只能选择刚才真实搜索返回的候选。
- 禁止虚拟商品、充值、会员/订阅、金融、医药保健、烟酒、成人用品、旅行、服务、二手与拍卖。
- 提交订单前再次核对唯一 SKU 和结算总额。
- 下单前写入任务锁；若提交瞬间崩溃，重复任务会停下来要求检查，不会盲目再下一单。

这些边界由程序执行，不依赖模型是否记得提示词。

## 失败状态

- 本地执行器离线：立即拒绝调用；
- 页面或金额无法核对：停止，不提交；
- 需要短信、验证码或支付密码：停止并返回需要验证；
- 结算页混入其他 SKU：停止；
- 同一任务已有不确定记录：停止，防止重复下单。

## Zeabur 环境变量

```text
OMBRE_JD_SHOPPING_ENABLED=1
OMBRE_JD_WORKER_TOKEN=<独立的高强度随机令牌>
OMBRE_JD_MAX_BUDGET_CNY=300
OMBRE_JD_TASK_TIMEOUT_SECONDS=100
OMBRE_JD_WORKER_TTL_SECONDS=45
```

MCP 地址是 `https://你的域名/api/jd-shopping/mcp`，由已有的 `OMBRE_GATEWAY_TOKEN` 保护。本地 worker 使用单独的 `OMBRE_JD_WORKER_TOKEN`。两者都不要复用 OpenRouter API Key。

## 工具管理页导入配置

```json
{
  "mcpServers": {
    "ombre-jd-shopping": {
      "transport": "streamable-http",
      "url": "https://你的域名/api/jd-shopping/mcp",
      "headers": {
        "x-api-key": "${secret:ombre-jd-shopping.gateway_token}"
      },
      "enabled": true,
      "autonomy": "chat_only",
      "categories": ["misc"],
      "allowedTools": []
    }
  }
}
```

导入后通过凭据框保存 `gateway_token`，测试连接，再勾选两个工具。平时不用时把自主权改为“停用”；要购物时再改回“仅对话可用”。
