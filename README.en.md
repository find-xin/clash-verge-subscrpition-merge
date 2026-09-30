# Clash Verge Multi-Subscription Merger

This command-line script merges any number of local Clash/Mihomo subscription YAML files into one configuration, including proxies, DNS settings, rules, and rule-provider definitions.

## Requirements

- Python 3.10 or newer
- PyYAML (`python -m pip install PyYAML`)

## Usage

Run the script from its directory. Quote paths that contain spaces.

```powershell
python .\clash_subscription_updater.py `
  "C:\Subscriptions\one.yaml" `
  "C:\Subscriptions\two.yaml" `
  -o "C:\Subscriptions\merged.yaml"
```

Pass any number of input files. To add a subscription, add its path. By default, file names are used as source labels and prefixed to proxy names. Labels can be specified explicitly in the same order as the files:

```powershell
python .\clash_subscription_updater.py `
  "C:\Subscriptions\one.yaml" "C:\Subscriptions\two.yaml" `
  --labels SourceOne SourceTwo `
  -o "C:\Subscriptions\merged.yaml"
```

On macOS or Linux, use the same command with `python3` and POSIX paths. If `-o` is omitted, the output is `clash-verge-merged.yaml` in the current directory.

## Merge behavior

- Creates latency-preferred and failover selectors, each with country groups for recognized regions (Hong Kong, Taiwan, Japan, the United States, Singapore, and the United Kingdom), an `其他地区节点` group, and a combined Japan/US/Singapore group. Subscription information entries are filtered from the proxy list.
- Health-check groups use a 200-second interval, a 1500 ms timeout, and `max-failed-times: 3` (forces a health check when the failure count exceeds the threshold). Latency-preferred groups use a 50 ms tolerance; failover groups use zero tolerance and select the lowest-latency available node.
- Preserves `tcp-concurrent` and `unified-delay` when any input enables them. Machine-specific listener ports and external-controller addresses are not copied from subscriptions.
- Keeps each subscription's internal rule order and applies input order as cross-subscription priority. Source `MATCH`/`FINAL` rules are replaced by a final `MATCH,代理模式`. Proxy-group actions are mapped to that selector; `DIRECT` and reject actions remain. Cross-subscription conflicts with different effective actions are listed in a CSV file next to the output.
- Chooses the most complete DNS configuration as the base rather than blindly concatenating resolver lists. Fake-IP exclusions, fallback filters, and non-conflicting DNS policies are merged. Deprecated Mihomo `fallback-filter.geosite` entries are migrated to `nameserver-policy` entries such as `geosite:gfw`, using the selected base's fallback resolvers. Existing explicit policies take precedence. DNS IPv6 is disabled if any input disables it.
- Prefixes rule-provider names with the source label to avoid collisions.

## Updating subscriptions

The script reads local files only; it does not download or periodically refresh subscriptions. After updating subscriptions, run it again with the updated, complete YAML files (not provider-only files).

Generated YAML files contain proxy credentials. Keep them private. The repository's `.gitignore` excludes YAML and CSV files by default.

