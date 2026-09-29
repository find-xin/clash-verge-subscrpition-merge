#!/usr/bin/env python3
"""Merge multiple local Clash/Mihomo subscription YAML files."""
from __future__ import annotations

import argparse
import copy
import csv
import re
import sys
import tempfile
from pathlib import Path


def load_yaml_module():
    try:
        import yaml
        return yaml
    except ImportError as exc:
        raise RuntimeError("缺少 PyYAML。请先运行：python -m pip install PyYAML") from exc


def safe_key(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_") or "source"


def dns_strategy_score(dns):
    """Prefer a coherent resolver setup over simply accumulating servers."""
    score = 0
    score += 4 if dns.get("respect-rules") is True else 0
    score += 3 if dns.get("proxy-server-nameserver") else 0
    score += 2 if any(str(s).startswith(("https://", "tls://", "quic://")) for s in dns.get("nameserver", [])) else 0
    score += 1 if dns.get("default-nameserver") else 0
    score += 1 if dns.get("fallback") else 0
    score += 1 if dns.get("fallback-filter") else 0
    score += 1 if dns.get("enhanced-mode") == "fake-ip" else 0
    return score


def append_unique(target, values):
    if not isinstance(target, list):
        target = []
    if isinstance(values, list):
        for value in values:
            if value not in target:
                target.append(copy.deepcopy(value))
    return target


def normalize_dns(configs, labels):
    entries = [(i, c["dns"]) for i, c in enumerate(configs) if isinstance(c.get("dns"), dict)]
    if not entries:
        return {}, "未提供 DNS 配置"

    # Choose the most complete source as the DNS strategy base. This avoids
    # mixing unrelated nameserver/fallback lists from every subscription.
    base_index, base_dns = max(entries, key=lambda pair: dns_strategy_score(pair[1]))
    dns = copy.deepcopy(base_dns)
    source_dns = [entry for _i, entry in entries]
    dns["enable"] = any(bool(item.get("enable")) for item in source_dns)
    dns["ipv6"] = all(bool(item.get("ipv6", False)) for item in source_dns)

    # Resolver endpoint lists are a coordinated strategy: keep the chosen
    # source's list, using another source only if the selected profile omits it.
    for key in ("default-nameserver", "nameserver", "proxy-server-nameserver", "fallback"):
        if not dns.get(key):
            replacement = next((item.get(key) for item in source_dns if item.get(key)), None)
            if replacement:
                dns[key] = copy.deepcopy(replacement)

    # Merge fake-IP exclusions and fallback filters, which are additive rules.
    for key in ("fake-ip-filter",):
        merged = copy.deepcopy(dns.get(key, []))
        for item in source_dns:
            merged = append_unique(merged, item.get(key))
        if merged:
            dns[key] = merged

    filters = copy.deepcopy(dns.get("fallback-filter", {}))
    if not isinstance(filters, dict):
        filters = {}
    for item in source_dns:
        other = item.get("fallback-filter")
        if not isinstance(other, dict):
            continue
        for key, value in other.items():
            if key in {"ipcidr", "domain"}:
                filters[key] = append_unique(filters.get(key, []), value)
            elif key == "geosite":
                # fallback-filter.geosite is deprecated by Mihomo. Its intent
                # is to query these domain sets via fallback resolvers, so
                # migrate it below to nameserver-policy.
                continue
            elif key == "geoip":
                filters[key] = bool(filters.get(key, False) or value)
            elif key not in filters:
                filters[key] = copy.deepcopy(value)
    if filters:
        dns["fallback-filter"] = filters

    # Merge non-conflicting domain policies. The selected DNS strategy wins
    # when two subscriptions define the same policy key differently.
    for key in ("nameserver-policy", "proxy-server-nameserver-policy"):
        policies = copy.deepcopy(dns.get(key, {}))
        if not isinstance(policies, dict):
            policies = {}
        for item in source_dns:
            values = item.get(key)
            if isinstance(values, dict):
                for domain, server in values.items():
                    policies.setdefault(domain, copy.deepcopy(server))
        if policies:
            dns[key] = policies

    # Modern equivalent of fallback-filter.geosite: route those domain sets
    # directly to the selected strategy's fallback resolvers. Explicit source
    # policies always win over this generated compatibility mapping.
    legacy_geosites = []
    for item in source_dns:
        old_filter = item.get("fallback-filter")
        if isinstance(old_filter, dict):
            legacy_geosites = append_unique(legacy_geosites, old_filter.get("geosite"))
    if legacy_geosites:
        policies = copy.deepcopy(dns.get("nameserver-policy", {}))
        if not isinstance(policies, dict):
            policies = {}
        fallback_servers = dns.get("fallback")
        if fallback_servers:
            for geosite in legacy_geosites:
                policy_key = str(geosite)
                if not policy_key.lower().startswith("geosite:"):
                    policy_key = f"geosite:{policy_key}"
                policies.setdefault(policy_key, copy.deepcopy(fallback_servers))
            dns["nameserver-policy"] = policies
        else:
            print(
                "警告：输入 DNS 使用了已弃用的 fallback-filter.geosite，"
                "但合并 DNS 没有 fallback 服务器，无法迁移为 nameserver-policy；已省略该过滤项。",
                file=sys.stderr,
            )

    # Do not leave an empty or deprecated geosite property behind.
    if isinstance(dns.get("fallback-filter"), dict):
        dns["fallback-filter"].pop("geosite", None)
        if not dns["fallback-filter"]:
            dns.pop("fallback-filter")

    # respect-rules needs a bootstrap resolver for proxy hostnames.
    wants_respect_rules = any(item.get("respect-rules") is True for item in source_dns)
    has_proxy_resolver = bool(dns.get("proxy-server-nameserver")) or any(
        item.get("proxy-server-nameserver") for item in source_dns
    )
    dns["respect-rules"] = bool(wants_respect_rules and has_proxy_resolver)
    if dns["respect-rules"] and not dns.get("proxy-server-nameserver"):
        dns["proxy-server-nameserver"] = copy.deepcopy(next(
            item["proxy-server-nameserver"] for item in source_dns if item.get("proxy-server-nameserver")
        ))

    return dns, labels[base_index]


BUILTIN_POLICIES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL", "BLOCK", "REJECT-443", "REJECT-TINYGIF"}


def rewrite_rule(rule, provider_map):
    if isinstance(rule, (list, tuple)):
        parts = [str(x) for x in rule]
    elif isinstance(rule, str):
        # Split only top-level commas; AND/OR/NOT rules can contain commas
        # inside parenthesized sub-rules.
        parts = []
        current = []
        depth = 0
        quote = None
        escaped = False
        for char in rule:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\":
                current.append(char)
                escaped = True
                continue
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
                continue
            if char in {"'", '"'}:
                quote = char
                current.append(char)
            elif char == "(":
                depth += 1
                current.append(char)
            elif char == ")":
                depth = max(0, depth - 1)
                current.append(char)
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        parts.append("".join(current).strip())
    else:
        return None
    if not parts:
        return None
    if parts[0].upper() in {"MATCH", "FINAL"}:
        return None

    if len(parts) < 3:
        return None

    source_action = parts[2]
    # Common Mihomo rule types have one condition field, followed by policy
    # and optional flags such as no-resolve. Include flags in the key so rules
    # with different matching behavior are not mistakenly treated as equal.
    match_key = (
        parts[0].strip().upper(),
        parts[1].strip().casefold(),
        tuple(part.strip().casefold() for part in parts[3:]),
    )
    effective_action = source_action if source_action.strip().upper() in BUILTIN_POLICIES else "代理模式"
    if parts[0].upper() in {"RULE-SET", "RULESET"} and len(parts) > 1:
        parts[1] = provider_map.get(parts[1], parts[1])
    if len(parts) > 2 and parts[2].upper() not in BUILTIN_POLICIES:
        parts[2] = "代理模式"
    return ",".join(parts), match_key, source_action, effective_action


def node_names(proxies, pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    return [p["name"] for p in proxies if rx.search(p["name"])]


INFO_NODE_PATTERN = re.compile(
    r"(剩余流量|套餐到期|到期时间|有效期至|流量重置|流量查询|官网地址|官网[:：]|网址[:：]|使用说明|订阅公告)",
    re.IGNORECASE,
)


def is_subscription_info_node(name):
    return bool(INFO_NODE_PATTERN.search(name))


def health_group(name, members, tolerance):
    return {"name": name, "type": "url-test", "proxies": members,
            "url": "https://www.gstatic.com/generate_204", "interval": 200,
            "timeout": 500, "max-failed-times": 3, "lazy": True,
            "tolerance": tolerance}


def build_config(configs, labels):
    proxies = []
    source_keys = [safe_key(label) for label in labels]
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("来源标签转换后发生重名；请为每个订阅设置不同标签")
    for config, label in zip(configs, labels):
        for raw in config["proxies"]:
            if isinstance(raw, dict) and raw.get("name"):
                proxy = copy.deepcopy(raw)
                original_name = str(proxy["name"])
                if is_subscription_info_node(original_name):
                    continue
                proxy["name"] = f"[{label}] {original_name}"
                proxies.append(proxy)
    if not proxies:
        raise ValueError("输入文件中没有找到带名称的代理节点")
    names = [p["name"] for p in proxies]
    if len(names) != len(set(names)):
        raise ValueError("发现重复节点名称；请使用不同的来源标签")

    health_groups = []
    country_groups = []
    classified_names = set()
    patterns = [
        ("香港节点", r"(香港|🇭🇰|\bHK[0-9]*|Hong\s*Kong)"),
        ("台湾节点", r"(台湾|台灣|🇹🇼|\bTW[0-9]*|Taiwan)"),
        ("日本节点", r"(日本|🇯🇵|\bJP[0-9]*|Japan)"),
        ("美国节点", r"(美国|美國|🇺🇸|United\s*States|\bUSA?\b|\bUS[0-9]*)"),
        ("新加坡节点", r"(新加坡|🇸🇬|Singapore|\bSG[0-9]*)"),
        ("英国节点", r"(英国|英國|🇬🇧|United\s*Kingdom|Britain|London|\bUK[0-9]*|\bGB[0-9]*)"),
    ]
    for name, pattern in patterns:
        members = node_names(proxies, pattern)
        if members:
            classified_names.update(members)
            latency_name = f"{name}-延迟优选"
            failover_name = f"{name}-故障切换"
            # Ordinary latency mode avoids unnecessary flips for tiny gains;
            # failover mode uses strict URLTest selection among live members.
            health_groups.append(health_group(latency_name, members, 50))
            health_groups.append(health_group(failover_name, members, 0))
            country_groups.append(name)

    # Keep nodes with unrecognized region naming accessible rather than
    # silently leaving them out of the selectable country groups.
    other_members = [proxy["name"] for proxy in proxies if proxy["name"] not in classified_names]
    if other_members:
        health_groups.append(health_group("其他地区节点-延迟优选", other_members, 50))
        health_groups.append(health_group("其他地区节点-故障切换", other_members, 0))
        country_groups.append("其他地区节点")
    combo = node_names(proxies, r"(日本|🇯🇵|\bJP[0-9]*|Japan|美国|美國|🇺🇸|United\s*States|\bUSA?\b|\bUS[0-9]*|新加坡|🇸🇬|Singapore|\bSG[0-9]*)")
    if combo:
        health_groups.append(health_group("日美新代理组-延迟优选", combo, 50))
        health_groups.append(health_group("日美新代理组-故障切换", combo, 0))
        country_groups.insert(0, "日美新代理组")

    # Rules target one root selector. The selected policy mode then exposes
    # country choices directly.
    latency_countries = [f"{name}-延迟优选" for name in country_groups]
    failover_countries = [f"{name}-故障切换" for name in country_groups]
    groups = [
        {"name": "代理模式", "type": "select", "proxies": ["延迟优选", "故障切换"]},
        {"name": "延迟优选", "type": "select", "proxies": latency_countries + ["DIRECT"]},
        {"name": "故障切换", "type": "select", "proxies": failover_countries + ["DIRECT"]},
    ] + health_groups

    providers, provider_maps = {}, []
    for config, key in zip(configs, source_keys):
        mapping = {}
        for name, provider in (config.get("rule-providers") or {}).items():
            new_name = f"{key}_{name}"
            mapping[str(name)] = new_name
            cloned = copy.deepcopy(provider)
            if isinstance(cloned, dict):
                cloned["path"] = f"./rule_providers/{new_name}.yaml"
            providers[new_name] = cloned
        provider_maps.append(mapping)

    rules, first_rule_by_match = [], {}
    conflicts = []
    reported_conflicts = set()
    for source_index, (config, mapping, label) in enumerate(zip(configs, provider_maps, labels)):
        for rule in config.get("rules", []) or []:
            rewritten = rewrite_rule(rule, mapping)
            if not rewritten:
                continue
            text, match_key, source_action, effective_action = rewritten
            original_rule = ",".join(str(part) for part in rule) if isinstance(rule, (list, tuple)) else str(rule)
            previous = first_rule_by_match.get(match_key)
            if previous:
                if previous["source_index"] == source_index:
                    # Never reorder or collapse rules within one subscription.
                    rules.append(text)
                    continue
                if previous["effective_action"].strip().casefold() != effective_action.strip().casefold():
                    conflict_signature = (
                        match_key, previous["source_index"], source_index,
                        effective_action.strip().casefold(),
                    )
                    if conflict_signature not in reported_conflicts:
                        reported_conflicts.add(conflict_signature)
                        conflicts.append({
                            "matching_condition": ",".join(match_key[:2]),
                            "winner_source": previous["source"],
                            "winner_rule": previous["original_rule"],
                            "winner_action": previous["source_action"],
                            "winner_effective_action": previous["effective_action"],
                            "conflicting_source": label,
                            "conflicting_rule": original_rule,
                            "conflicting_action": source_action,
                            "conflicting_effective_action": effective_action,
                            "resolution": f"保留输入顺序靠前的订阅：{previous['source']}",
                        })
                # Keep the first rule for a matching condition. This preserves
                # input-order priority and avoids silently letting a later
                # subscription override it.
                continue
            first_rule_by_match[match_key] = {
                "source_index": source_index,
                "source": label,
                "original_rule": original_rule,
                "source_action": source_action,
                "effective_action": effective_action,
            }
            rules.append(text)
    rules.append("MATCH,代理模式")
    merged_dns, dns_source = normalize_dns(configs, labels)
    out = {"mixed-port": 7897, "allow-lan": False, "mode": "rule", "log-level": "info", "ipv6": False,
           "dns": merged_dns, "proxies": proxies, "proxy-groups": groups, "rules": rules}
    if providers:
        out["rule-providers"] = providers
    out["profile"] = {"store-selected": True}
    return out, conflicts, dns_source


def main():
    parser = argparse.ArgumentParser(description="合并一个或多个本地 Clash/Mihomo 订阅 YAML 文件。")
    parser.add_argument("files", nargs="+", type=Path, help="已下载的订阅 YAML 文件，可传任意多个")
    parser.add_argument("-o", "--output", type=Path, default=Path("clash-verge-merged.yaml"), help="输出路径，默认当前目录下 clash-verge-merged.yaml")
    parser.add_argument("--labels", nargs="*", help="每个输入文件的来源标签，顺序与文件参数一致；默认使用文件名")
    args = parser.parse_args()
    if args.labels is not None and len(args.labels) != len(args.files):
        parser.error("--labels 标签数量必须与输入文件数量相同")
    yaml = load_yaml_module()
    configs, labels = [], []
    for index, path in enumerate(args.files):
        if not path.is_file():
            raise FileNotFoundError(f"找不到输入文件：{path}")
        config = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict) or not isinstance(config.get("proxies"), list) or not config["proxies"]:
            raise ValueError(f"{path} 不是包含非空 proxies 列表的 Clash YAML")
        configs.append(config)
        labels.append(args.labels[index] if args.labels else path.stem)
    if len({label.casefold() for label in labels}) != len(labels):
        parser.error("来源标签必须唯一；若文件名重复，请用 --labels 指定不同标签")
    result, conflicts, dns_source = build_config(configs, labels)
    serialized = "# 由 clash_subscription_updater.py 根据命令行输入文件生成。\n"
    serialized += yaml.safe_dump(result, allow_unicode=True, sort_keys=False, default_flow_style=False)
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    conflict_path = target.with_name(target.stem + "-规则冲突.csv")
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with open(fd, "w", encoding="utf-8", newline="\n", closefd=True) as stream:
            stream.write(serialized)
        Path(temp_name).replace(target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    with conflict_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "matching_condition", "winner_source", "winner_rule", "winner_action", "winner_effective_action",
            "conflicting_source", "conflicting_rule", "conflicting_action", "conflicting_effective_action", "resolution",
        ])
        writer.writeheader()
        writer.writerows(conflicts)
    print(f"已合并 {len(configs)} 个订阅文件，节点 {len(result['proxies'])} 个，规则 {len(result['rules'])} 条。")
    print(f"输出文件：{target}")
    print(f"DNS策略基于订阅：{dns_source}")
    print(f"检测到 {len(conflicts)} 条跨订阅动作冲突；明细：{conflict_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"合并失败：{exc}", file=sys.stderr)
        raise SystemExit(1)

