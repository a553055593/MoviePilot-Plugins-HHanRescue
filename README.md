# MoviePilot-Plugins-HHanRescue

HHanClub（hhanclub.net）保种区积分助手 · MoviePilot **v2** 插件仓库。

把本仓库添加到 MoviePilot 的「插件市场」设定（`PLUGIN_MARKET`）即可安装 **HHanClub 保种积分助手**（HHanRescue）。

## 安装

1. MoviePilot → 设定 → 插件 → 插件市场，添加本仓库地址：
   `https://github.com/a553055593/MoviePilot-Plugins-HHanRescue`
   （Docker 环境变量方式：`PLUGIN_MARKET=<官方市场地址>,https://github.com/a553055593/MoviePilot-Plugins-HHanRescue`）
2. 重启 / 刷新插件市场，搜索 `HHanRescue` 或标签「保种」
3. 点击安装 → 打开插件右下角设置 → 勾选「启用插件」→ 保存

## 功能

定时抓取 hhanclub.net 保种区（rescue.php）全部种子，按经站方规则原文审计、34/34 独立重算验证的积分模型计算每颗种子「加入你本人做种池后，总积分的净增量/天」，排序后自动把最划算的推送到 qBittorrent / Transmission。

- 池基线 A 和池积分倍率都自动读取你自己的 `mybonus.php`（带你的站点 Cookie），数值因人而异，与站方结算口径一致，**无需手动填写任何数值**
- 需要在 MoviePilot「站点管理」里配置过 HHanClub 站点（用它的 Cookie/UA），或手动填 Cookie
- 只做「下载种子推送下载器」这一个自动化动作，不做任何点赞/评论类操作（站规禁止）
- Cookie 只存 MoviePilot 插件配置库，不进日志和通知

## 数值口径（做种积分只有两项，常数全部出自 wiki 规则原文）

做种积分 = 基础项（mybonus.php「基本奖励」行基础憨豆，无加成，上限 50）
        + 保种区档位倍率 × 基础量。仅此两项。

```
A_i   = (1 − 10^(−周数/8)) × GB × (1 + √2×10^(−(当前做种人数−1)/9))
B(池) = 25 × (2/π) × arctan(A池/300 − 5) + 20
净增量 Δ = [B(池+i)×加权积分倍率_new − B(池)×池积分倍率] × 24
池积分倍率 = 基础项速率 / B(池)（自动读取 mybonus.php，无需手填）
档位（下载瞬间人数锁定）：≤1人→2×；2-3人→1.75×；4-5人→1.5×
```

## 仓库结构

```
package.v2.json                     # v2 市场索引（MP 从 main 分支读取）
plugins.v2/hhanrescue/__init__.py   # 插件本体（安装时写入 app/plugins/hhanrescue）
icons/hhanrescue.svg                # 市场图标
```

## 问题排查

安装后插件列表不显示时：

```bash
# 成功加载的日志行：加载插件：HHanRescue 版本号：1.0.0
docker exec -it <mp> sh -c "grep -iE 'hhan' /config/logs/moviepilot.log | tail -n 20"
docker exec -it <mp> ls /app/app/plugins | grep -i hhan
```

详情见插件内 README。
