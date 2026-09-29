# Clash Verge 多订阅合并脚本

脚本读取你指定的一个或多个本地订阅 YAML 文件，合并节点、DNS、规则和规则集定义，然后生成一份 Clash/Mihomo 配置。订阅增加时，在命令中多加一个文件路径即可。

## 准备

需要 Python 3.10 或更新版本，以及 PyYAML。PyYAML 只需安装一次：

```powershell
python -m pip install PyYAML
```

## 使用

在 PowerShell 中运行，路径请用引号括起来：

```powershell
python "C:\Users\Lenovo\Documents\Codex\2026-09-29\zhe\outputs\clash_subscription_updater.py" `
  "C:\订阅\掌中世界.yaml" `
  "C:\订阅\飞鸟云.yaml" `
  "C:\订阅\良心云.yaml" `
  -o "C:\订阅\合并配置.yaml"
```

文件路径可以是任意数量。之后增加第四个订阅时，再添加一个输入文件路径即可。默认使用文件名作为来源标签并加在节点名前。也可以自定义标签：

```powershell
python "C:\Users\Lenovo\Documents\Codex\2026-09-29\zhe\outputs\clash_subscription_updater.py" `
  "C:\订阅\a.yaml" "C:\订阅\b.yaml" `
  --labels 掌中世界 飞鸟云 `
  -o "C:\订阅\合并配置.yaml"
```

省略 `-o` 时，输出到当前目录的 `clash-verge-merged.yaml`。生成后，在 Clash Verge 中导入或打开这份本地配置。

## 合并行为

- 代理组包含总入口“代理模式”、两种模式组，以及每种模式各自的国家测速组。例如日本、美国分别生成“日本节点-延迟优选”“日本节点-故障切换”“美国节点-延迟优选”“美国节点-故障切换”。另有“其他地区节点”组，收纳名称无法识别国家的节点；订阅说明项（剩余流量、到期时间、官网地址等）会从节点列表中过滤。规则和 `MATCH` 兜底都指向“代理模式”，因此切换模式会影响代理流量。所有测速组使用 200 秒检测间隔、500 毫秒超时、连续失败 3 次。
- “延迟优选”使用 `url-test`，自动选择延迟较低的可用节点，并设置 50 毫秒切换容差，减少很小延迟差导致的频繁切换。“故障切换”也使用 `url-test`，容差设为 0，持续选择当前可用节点中延迟最低的一台。它会在检测到当前节点不可用时换到其他可用节点，但也可能因测速结果变化而主动换到更快节点；Mihomo 原生 `fallback` 只能按列表顺序选可用节点，不能按实时延迟对备用节点排序。测速组启用 `lazy: true`，未被当前选择链路使用的组不会持续重复探测。
- 按命令行输入顺序决定订阅优先级，并保留每份订阅内部的规则顺序。相同匹配条件跨订阅重复时，优先保留先输入订阅的规则；只有合并后实际动作不同（例如 `DIRECT` 对代理、`DIRECT` 对 `REJECT`）才会在输出 YAML 旁生成 `<输出文件名>-规则冲突.csv`。源订阅中不同的代理组名称最终都会映射到“代理模式”，因此不会被误报成实际动作冲突。清单列出匹配条件、优先规则、冲突规则和合并后动作，可用 Excel 打开。源文件的 `MATCH`/`FINAL` 兜底规则会跳过，最后添加 `MATCH,代理模式`。规则匹配条件和 `DIRECT`/拒绝动作保留，其他代理组目标改为“代理模式”。源订阅专属代理策略组不会原样保留。
  - DNS 不再把所有订阅的 DNS 服务器地址盲目拼在一起，而是自动选配置较完整的一份作为主策略（优先考虑 `respect-rules`、节点域名解析服务器、加密 DNS 等）。其他订阅的 fake-IP 排除项、fallback 过滤条件和不冲突的域名解析策略会合并；同一域名策略冲突时主 DNS 配置优先。已把 Mihomo 弃用的 `fallback-filter.geosite` 自动迁移为 `nameserver-policy`（如 `geosite:gfw` 指向主策略的 fallback DNS），已有的同键手工解析策略优先保留；因此生成配置不会继续输出弃用字段。以当前三个订阅为例，飞鸟云作为主策略，保留其 DoH nameserver、fallback 和 proxy-server-nameserver，同时保留良心云的 Google/Facebook/YouTube fallback 域名过滤条件。只要任一输入配置禁用了 IPv6，合并结果就禁用 IPv6。
- 规则集名称按来源加前缀，避免不同订阅中同名规则集互相覆盖。

该脚本只处理指定的本地文件，不会自动下载订阅或定时更新。订阅更新后，重新运行命令即可生成新版配置；请确保输入的是更新后的完整 YAML，而不是只含节点的 provider 列表。

生成的 YAML 含有节点凭据，请妥善保管。

