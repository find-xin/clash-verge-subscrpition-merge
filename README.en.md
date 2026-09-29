# Clash Verge Multi-Subscription Merger

This command-line script merges any number of local Clash/Mihomo subscription YAML files into one configuration, including proxies, DNS settings, rules, and rule-provider definitions. Add another input path whenever you want to include another subscription.

[中文说明](README.md)

## Requirements

- Python 3.10 or newer
- PyYAML

Install the dependency once:

```powershell
python -m pip install PyYAML
```

## Usage

Run the script from the directory where you saved it. Quote file paths, especially when they contain spaces.

PowerShell:

```powershell
python .\clash_subscription_updater.py `
  "C:\Subscriptions\subscription-one.yaml" `
  "C:\Subscriptions\subscription-two.yaml" `
  "C:\Subscriptions\subscription-three.yaml" `
  -o "C:\Subscriptions\merged.yaml"
```

You can pass any number of input files. To add a fourth subscription, add its path to the command. By default, each file name is used as a source label and added to proxy names. You can specify labels explicitly; their order must match the input files:

```powershell
python .\clash_subscription_updater.py `
  "C:\Subscriptions\one.yaml" "C:\Subscriptions\two.yaml" `
  --labels SourceOne SourceTwo `
  -o "C:\Subscriptions\merged.yaml"
```

If `-o` is omitted, the script writes `clash-verge-merged.yaml` in the current directory. Import or open the generated file in Clash Verge.

macOS/Linux shell example:

```bash
python3 clash_subscription_updater.py \
  ~/Subscriptions/one.yaml \
  ~/Subscriptions/two.yaml \
  -o ~/Subscriptions/merged.yaml
```

## What gets merged

- **Proxy groups:** Creates a top-level selector named `代理模式`, with `延迟优选` and `故障切换` modes, then country groups under each mode. Recognized regions include Hong Kong, Taiwan, Japan, the United States, Singapore, and the United Kingdom. It also creates an `其他地区节点` group for unrecognized names and a combined Japan/US/Singapore group. Subscription information entries (such as traffic or expiry notices) are filtered out of the proxy list.
- **Health checks:** Generated URL-test groups use a 200-second interval, a 500 ms timeout, and three consecutive failures. Latency-preferred groups use a 50 ms tolerance to reduce switching for tiny latency differences. Failover groups use zero tolerance and choose the lowest-latency available node; they may also switch when another node becomes faster. Groups use `lazy: true`.
- **Rules:** Input order defines subscription priority, while each subscription's internal rule order is kept. Source `MATCH`/`FINAL` rules are skipped, and a final `MATCH,代理模式` rule is added. Other proxy-group targets are rewritten to `代理模式`, while `DIRECT` and reject actions are retained. When identical matching conditions across subscriptions have different effective actions, a CSV conflict report is written next to the output. Source-specific proxy groups are not preserved.
- **DNS:** The script chooses the most complete input DNS configuration as the base instead of blindly concatenating all resolver lists. It merges fake-IP exclusions, fallback filters, and non-conflicting DNS policies; the selected base wins on policy conflicts. Deprecated Mihomo `fallback-filter.geosite` values are migrated to `nameserver-policy` entries such as `geosite:gfw`, using the selected base's fallback resolvers. Existing explicit policies take precedence. IPv6 is disabled in the merged DNS configuration if any input disables it.
- **Rule providers:** Provider names are prefixed by source to avoid collisions between subscriptions.

## Updating subscriptions

The script reads local files only. It does not download or periodically refresh subscriptions. After updating your subscriptions, run it again with the updated, complete YAML files (not provider-only files) to regenerate the merged configuration.

Generated YAML files contain proxy credentials. Keep them private. The repository's `.gitignore` excludes YAML and CSV files by default to help prevent accidental uploads.
