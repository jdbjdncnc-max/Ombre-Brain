# Ombre 京东购物助手

它由两部分组成：Ombre 通过普通 MCP 工具决定搜索词和候选 SKU，本机 Edge 负责访问京东、核对额度并处理订单。京东登录信息始终留在你的电脑里。购物助手通过微软官方 Playwright Extension 连接你平时使用的 Edge，不再启动独立的自动化浏览器。

## 购物时需要保持什么

- Windows 电脑没有关机或睡眠；
- `开始惊喜购物.ps1` 的窗口仍在运行；
- 平时使用的 Edge 已打开，并保留一个已登录的京东标签页；
- Playwright Extension 保持启用。

平时不用时可以关掉脚本，并在 Ombre 工具管理页把 `ombre-jd-shopping` 设为“停用”。停用后购物工具不会交给对话模型。

## 第一次设置

### 1. Zeabur 环境变量

```text
OMBRE_JD_SHOPPING_ENABLED=1
OMBRE_JD_WORKER_TOKEN=一串只有你知道的长随机字符
OMBRE_JD_MAX_BUDGET_CNY=300
```

`OMBRE_JD_WORKER_TOKEN` 是本机 worker 和网关之间的专用令牌，不是 OpenRouter Key，也不是网关令牌。建议至少使用 32 位随机字符。

部署后，可用原来的网关令牌访问 `/api/jd-shopping/status` 查看是否启用。

### 2. 在 Ombre 导入普通 MCP

把下面内容中的域名换成自己的域名，然后粘贴到工具管理页：

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

导入后：

1. 在凭据框中，名称填写 `gateway_token`，内容填写 APP 正在使用的网关令牌；
2. 点击“测试连接”；
3. 勾选 `search_surprise_gift` 和 `submit_authorized_jd_order`；
4. 自主权保持“仅对话可用”，然后保存。

不用购物时，把自主权改成“停用”并保存即可。

### 3. 设置本地助手

右键 `开始设置.ps1`，选择“使用 PowerShell 运行”。它会安装依赖并打开 `config.local.json`。

还需要从 Edge 扩展商店安装微软官方 **Playwright Extension**。请把它安装在你平时登录京东所用的同一个 Edge 个人资料中。

需要修改：

- `gateway.url`：你的 Ombre Zeabur 地址；
- `perOrderCapCny`：单笔最多花多少；
- `monthlyCapCny`：每月最多花多少；
- `maxOrdersPerMonth`：每月最多几单；
- `expiresAt`：授权到哪天失效。

不需要另填 OpenRouter 模型或 API Key，模型仍使用 Ombre 原来的设置。

### 4. 登录、授权并运行

运行 `开始惊喜购物.ps1`：

1. 粘贴与 Zeabur 相同的 `OMBRE_JD_WORKER_TOKEN`；
2. 输入一次“授权”，建立本地额度；
3. 在平时使用的 Edge 打开并登录京东；
4. Playwright 授权页出现后，选择该京东标签页并点击 `Allow & select`；
5. 保持 PowerShell、Edge 和京东标签页运行。

## 程序会自动停止的情况

- 价格或结算总额超出单笔/月度额度；
- 本月订单数已满；
- 结算页同时选中了目标以外的商品；
- 商品属于禁止范围；
- 京东要求验证码、短信或支付密码；
- 京东登录已失效（必须存在真实登录凭证，访客 Cookie 不再误判为已登录）；
- 浏览器扩展断开或已授权的京东标签页被关闭；
- 京东提示“访问过于频繁”（自动冷却 30 分钟，期间不会继续请求）；
- 同一个任务留下了不确定的下单记录。

程序会先取消勾选购物车里的旧商品；只有结算页仍然混入其他 SKU 时才停止。

若看到搜索冷却提示，请保持助手关闭或在线等待，不要反复重启。冷却记录保存在本机，重启也不会绕过京东限制。

## 支付说明

程序会先提交一件商品的订单，再尝试使用京东余额。若京东要求额外验证，订单可能已经建立但尚未付款；程序不会绕过验证，也不会保存支付密码。

## 以后全本地使用

`npm run mcp` 会启动标准 `stdio` MCP，提供：

- `search_surprise_gift`
- `submit_authorized_jd_order`

这适合以后把 Ombre 后端和浏览器放到同一台常驻设备。当前 Zeabur 版本使用标准 Streamable HTTP MCP 加本地任务桥，因为云端无法直接启动你 Windows 上的浏览器。

## 查看本地额度状态

```powershell
npm run status
```

## 现实限制

- 京东页面改版后，浏览器定位规则可能需要更新；
- 地址、库存、运费和优惠券会影响最终结算价；
- Codex 沙箱里打开 Edge 授权页可能报 `spawn EPERM`，请在普通 Windows PowerShell 中实际运行；
- 这套程序不伪装设备指纹、不绕过验证码。
