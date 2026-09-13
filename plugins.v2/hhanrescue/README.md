# HHanClub 保种积分助手 · MoviePilot v2 插件安装说明

插件目录：`hhanrescue/`（本文件夹）；打包好的安装包：上级目录的 **`HHanRescue.zip`**（压缩包名 = 插件类名，符合 MP 上传安装规范）

## 安装方式

### 方式〇：插件市场仓库（推荐，已发布）

插件已发布到 GitHub 仓库 **`a553055593/MoviePilot-Plugins-HHanRescue`**，走 MoviePilot 官方市场安装流程：

1. MoviePilot → 设定 → 插件 → **插件市场**，添加仓库地址：
   `https://github.com/a553055593/MoviePilot-Plugins-HHanRescue`
   （或 Docker 环境变量 `PLUGIN_MARKET=<原有市场地址>,https://github.com/a553055593/MoviePilot-Plugins-HHanRescue`）
2. 保存后重启 MoviePilot（或刷新插件市场）
3. 市场里搜 `HHanRescue` 或标签「保种」→ 点安装 → 右下角设置勾选「启用插件」→ 保存

官方安装流程会自动把目录写成全小写 `hhanrescue` 并登记到已安装列表，不会出现上传安装那种「装了不显示」的大写目录问题。

### 方式一：ZIP 上传安装

1. MoviePilot → 插件市场 → 安装插件 → **上传** `HHanRescue.zip`（zip 内含 `hhanrescue/__init__.py`，继承 `_PluginBase`，无第三方依赖，无需 requirements.txt）
2. **首次安装请点击右下角设置打开「启用插件」→ 保存**
3. 如果提示「安装失败，状态码 404」：**重启 MoviePilot 生效上传 API**，再重新上传
4. 安装失败时先检查 zip 结构/错误信息，日志里有详细原因

### 方式二：本地复制（离线部署）

1. 把整个 `hhanrescue` 文件夹复制到 MoviePilot 的插件目录：
   ```
   config/plugins/hhanrescue/__init__.py
   ```
   （Docker 部署对应宿主机挂载的 `config/plugins/` 下）
2. 重启 MoviePilot（或插件管理页刷新）。
3. 「插件市场」→ 已安装插件里找到 **HHanClub 保种积分助手**，打开配置。

### 方式三：本地仓库（官方 v2 机制，推荐兜底）

上传安装不可用/装了不显示时，用官方 `PLUGIN_LOCAL_REPO_PATHS` 机制（不依赖任何第三方 API）：

1. 本仓库已备好 `../local-repo/` 目录（`package.v2.json` + `plugins.v2/hhanrescue/__init__.py`），整个文件夹拷到宿主机任意位置，例如 `/volume1/local-repo`
2. docker 部署把它挂进容器并加环境变量：
   ```yaml
   volumes:
     - /volume1/local-repo:/local-repo
   environment:
     - PLUGIN_LOCAL_REPO_PATHS=/local-repo
   ```
3. 重启 MoviePilot → 插件市场会出现本地仓库来源的 **HHanClub 保种积分助手** → 点安装（官方安装流程会自动写入安装列表并重载）
4. 装完打开右下角设置 → 启用插件 → 保存

## 装了不显示（v2 排查）

上传安装后列表里没有插件，按顺序查（`<mp>` 换成你的容器名）：

```bash
# ① 日志里搜插件名：成功会有一行「加载插件：HHanRescue 版本号：1.0.0」
docker exec -it <mp> sh -c "grep -iE 'hhan' /config/logs/moviepilot.log | tail -n 20"

# ② 查文件落点与大小写（v2 只认全小写目录 hhanrescue，且只在 /app/app/plugins）
docker exec -it <mp> ls /app/app/plugins | grep -i hhan
docker exec -it <mp> ls /config/plugins 2>/dev/null | grep -i hhan
```

| 现象 | 处理 |
|---|---|
| `/app/app/plugins/HHanRescue`（大写） | `docker exec -it <mp> mv /app/app/plugins/HHanRescue /app/app/plugins/hhanrescue` → 重启 |
| 只在 `/config/plugins/hhanrescue` | v2 不扫这里：`docker cp` 或复制到 `/app/app/plugins/hhanrescue` → 重启 |
| 两处都没有 | 上传 API 没解压成功 → 用上面「方式三」 |
| 日志有 ImportError | 把报错行发我 |

## 功能

定时抓取 hhanclub.net 保种区（rescue.php）全部种子，用经规则原文审计、34/34 独立重算验证的积分模型计算每颗种子「加入你本人做种池后，总积分的净增量/天」，排序后自动把最划算的推送到 qBittorrent / Transmission。

**数值因人而异**：排名用的池基线 A 和池积分倍率都是插件带你的 Cookie 去读你自己的 `mybonus.php` 自动算出来的（「基本奖励」行 A 值 + 顶部积分速率），无需手动填写或计算。保种多的用户池 A 大、每颗新种子的边际增量小；保种少的用户池 A 小、增量反而大——这正是 arctan 曲线的含义，跟站方结算口径一致。

## 配置

| 项 | 说明 |
|---|---|
| 启用插件 | 总开关 |
| 发送通知 | 运行结果推送到通知渠道 |
| 立即运行一次 | 保存后立即跑一轮（跑完自动复位） |
| HHanClub 站点 | 下拉选你在 MoviePilot 里配的 HHanClub 站点（自动用它的 Cookie/UA）；不选则用下面的手动域名+Cookie |
| 下载器 | Qbittorrent / Transmission |
| 保存路径 / qB 分类 | 留空用下载器默认 |
| 单次最多下载 | 默认 3 |
| 单种子体积上限 | GB，0 不限 |
| 积分/天 净增量门槛 | 默认 0（下载所有净增为正的） |
| 做种人数上限 | 默认 5（保种区规则：>5 人移出保种区） |
| 池基线 A | 留 0 自动读取 mybonus.php（默认）；手动填数字则强制使用 |
| 定时 Cron | 默认每天 14:10（保种区每天 14:00 更新） |

池积分倍率也是自动的：插件从 mybonus.php 顶部「你当前每小时能获取 N 个积分」读取站方算好的积分速率，除以 B(池) 得到倍率。**全程不需要你手填或自己计算任何数值。**

## 模型口径（做种积分只有两项，全部出自 wiki 规则原文）

做种积分构成（wiki《憨豆与做种积分》原文，仅此两项，没有其他）：
一、基础项 = 每小时获得憨豆「无加成」的部分，上限 50（= mybonus.php「基本奖励」行「基础憨豆」，该行系数恒为 1，即页面顶部「每小时能获取 N 个积分」的 N；官种/后宫/勋章等憨豆加成与积分无关）
二、保种区额外做种积分奖励 = 档位倍率 × 基础量
总做种积分 = 一 + 二

```
A_i   = (1 − 10^(−周数/8)) × GB × (1 + √2×10^(−(当前做种人数−1)/9))
B(池) = 25 × (2/π) × arctan(A池/300 − 5) + 20        [保种区 B0=25，+20 已由站方数字证实]
A池   = 你的全站做种池 A（mybonus.php「基本奖励」行） + 本种子 A_i

净增量排名：Δ = [B(池+i)×加权积分倍率_new − B(池)×池积分倍率] × 24
  池积分倍率   = 基础项速率 / B(池)（自动读取 mybonus.php，无需手填）
  加权倍率_new = (池倍率×A池 + 档位倍率×A_i) / (A池 + A_i)
档位（下载瞬间人数锁定）：≤1人→2×；2-3人→1.75×；4-5人→1.5×
```

排名含义：池饱和（B 接近 45/h 上限）时，只有档位倍率高于你现有池加权倍率的种子才会让总积分增长，4-5 人档反而稀释；池不饱和时全部为正增量，按大小排。

## 只做下载

插件唯一的自动化动作是**下载种子推送到下载器**（你明确要求的）。不做任何点赞/评论/发帖类操作（站规禁止脚本自动化此类行为）。Cookie 只存 MoviePilot 插件配置库，不进日志和通知。

## 验证过的真实数据（2026-09-11）

- 解析器干跑：rescue.php 快照 40/40 张卡片全解析成功（id/体积/时间/做种数一致）
- 池基线：mybonus.php 抓取「基本奖励」A=1,820.065 ✓（与页面顶部 36.952/h、B20(1820)+低保 0.02×327=36.952 精确吻合）
- 排名（池 A=1820，40/40 全为正增量）：Top3 = 194626（189GB/5人 +165/天）、129631（187GB/5人 +164/天）、73501（74GB/3人 +115/天）
