# -*- coding: utf-8 -*-
"""HHanClub 保种区积分最大化插件（MoviePilot v2）

抓取 hhanclub.net 保种区（rescue.php）全部种子，按「经 2026-09-14 真实结算
逐分反推验证」的积分模型计算每个种子的「做种积分净增量/天」，排序后把
最划算的种子自动推送到 qBittorrent / Transmission。只算积分。

做种积分构成（wiki《憨豆与做种积分》原文，仅此两项，没有其他）：
  一、基础项 = 每小时获得憨豆「无加成」的部分，上限 50
      = 0.02×做种数 + B20(A池)，其中 B20(A) = 20×(2/π)×arctan(A/300−5)+20
      （已对拍：0.02×328 + B20(2179.711) = 41.268 = 页面「每小时能获取 N 个积分」。
        基础项曲线 B0=20；B0=25 那条只属于保种区奖励口径，与基础项无关）
  二、保种区额外 = 档位倍率 × 0.25 × A_i × (当日做种小时/24)
      满时做种(h=24)即为：档位倍率 × 0.25 × A_i
      （由 2026-09-14 结算反推验证：
        56847  A=295.81 x1.75 h=20.77h → 112 分；80861 A=392.90 x1.5 h=15.15h → 93 分
        合计 205 积分 ✓，同式憨豆 221 ✓，平均时长 17.96h ≈ 结算日志 18h ✓）
  A_i = (1 − 10^(−周数/8)) × GB × (1 + √2×10^(−(下载时做种人数−1)/9))
      周数从种子发布时间起算（rescue.php 卡片上的时间即发布时间）
  档位（下载时人数锁定）：≤1人→2×；2-3人→1.75×；4-5人→1.5×

排名值 = ①基础项净增量×24 + ②满时保种区额外。实际结算②按当日实际做种
时长折算（每天 ≥18h 达标），①与保种区无关、由全部做种每小时自然累积。
池 A 自动读 mybonus.php「基本奖励」行，读取失败时排名仅按②（①按 0）。

只做「下载」这一个自动化动作（用户明确要求），不做任何点赞/评论类操作。
Cookie 只存 MoviePilot 插件配置库，不会写进任何日志或通知正文。
"""
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from apscheduler.triggers.cron import CronTrigger

from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo
from app.utils.http import RequestUtils
from app.utils.string import StringUtils

# 插件向下载器打的全局标签（用于识别本插件添加的种子）
HOMEPAGE = "https://hhanclub.net"
RESCUE_URL = f"{HOMEPAGE}/rescue.php"
DETAIL_URL = f"{HOMEPAGE}/details.php?id={{id}}&hit=1"
DOWNLOAD_URL = f"{HOMEPAGE}/download.php?id={{id}}"
# 用户憨豆页：「基本奖励」行 A 值 = 你的全站做种池 A（基础项曲线的自变量）
MYBONUS_URL = f"{HOMEPAGE}/mybonus.php"
PLUGIN_TAG = "HHanRescue"

# 档位积分倍率：{人数区间: 倍率}（保种区规则，按下载时人数锁定）
TIERS: List[Tuple[Tuple[int, int], float]] = [
    ((0, 1), 2.0),
    ((2, 3), 1.75),
    ((4, 5), 1.5),
]

# 基础项曲线常数（已对拍页面 41.268 精确成立）
COUNT_RATE = 0.02        # 每个做种种子 +0.02/h
BASE_CURVE_B0 = 20.0     # B20(A) = 20×(2/π)×arctan(A/300−5)+20
BASE_CURVE_L = 300.0
# 保种区日基础量系数：满 24h 做种的日基础量 = 0.25 × A_i
# （2026-09-14 结算反推：c=0.25 时两个种子隐含做种时长 20.77/15.15h，平均 17.96h
#   与结算日志「平均保种时间 18h」吻合；09-11 大样本 260 种/18h/1800 分量级亦自洽）
RESCUE_DAILY_C = 0.25


def tier_of(seeders: int) -> float:
    """按下载瞬间人数取积分档位倍率（0 做种并入 ≤1 人档）"""
    for (low, high), multiplier in TIERS:
        if low <= seeders <= high:
            return multiplier
    return 1.0


def seeder_factor(n: int) -> float:
    """A 公式人数衰减因子：1 + √2×10^(−(n−1)/9)"""
    return 1 + math.sqrt(2) * math.pow(10, -(n - 1) / 9)


def calc_a(gb: float, weeks: float, seeders: int) -> float:
    """A_i = (1−10^(−周数/8)) × GB × (1+√2×10^(−(人数−1)/9))"""
    return (1 - math.pow(10, -weeks / 8)) * gb * seeder_factor(seeders)


def base_rate_b(a: float) -> float:
    """基础项曲线（B0=20）：20×(2/π)×arctan(A/300−5)+20

    基础项/h = 0.02×做种数 + 本函数(A池)；对拍 0.02×328+B20(2179.711)=41.268 ✓。
    （旧版误用 B0=25 的保种区曲线算基础项池，已废弃）
    """
    return BASE_CURVE_B0 * (2 / math.pi) * math.atan(a / BASE_CURVE_L - 5) + 20


def _to_float(val: Any, default: float = 0.0) -> float:
    """容错转 float：前端文本框可能传来 '2,177.189'、'100GB'、''、None 等，一律不抛异常"""
    if val is None or isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip()
    if not text:
        return default
    # 去掉千分位逗号与单位尾巴
    text = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if not text or text in (".", "-"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _to_int(val: Any, default: int) -> int:
    """容错转 int：'3.5'→3、'abc'→默认值，不抛异常"""
    f = _to_float(val, default)
    return int(f)


class HHanRescue(_PluginBase):
    # 插件元信息
    plugin_name = "HHanClub 保种积分助手"
    plugin_desc = "按保种区积分模型（经真实结算反推验证）排序收益并自动下载最划算的保种种子。"
    plugin_version = "1.1.0"
    plugin_author = "a553055593"
    plugin_config_prefix = "hhanrescue_"
    plugin_order = 30
    auth_level = 1

    # 配置项
    _enabled = False
    _notify = False
    _onlyonce = False
    # 站点（MoviePilot 站点 ID），或 0 表示用下面的手动域名+Cookie
    _site_id = None
    _domain = ""
    _cookie = ""
    _ua = ""
    _downloader = ""
    _save_path = ""
    _qb_category = ""
    _max_count = 3
    _max_size_gb = 0
    _min_jf_day = 0.0
    _max_seeders = 5
    _cron = ""

    def __init__(self):
        super().__init__()
        self._siteoper = SiteOper()
        self._siteshelper = SitesHelper()

    def init_plugin(self, config: dict = None):
        """生效配置并注册定时服务"""
        if config:
            self._enabled = bool(config.get("enabled"))
            self._notify = bool(config.get("notify"))
            self._onlyonce = bool(config.get("onlyonce"))
            self._site_id = config.get("site_id") or None
            self._domain = str(config.get("domain") or "").strip().rstrip("/")
            self._cookie = str(config.get("cookie") or "").strip()
            self._ua = str(config.get("ua") or "").strip()
            self._downloader = str(config.get("downloader") or "").strip()
            self._save_path = str(config.get("save_path") or "").strip()
            self._qb_category = str(config.get("qb_category") or "").strip()
            self._max_count = _to_int(config.get("max_count"), 3)
            self._max_size_gb = _to_float(config.get("max_size_gb"), 0)
            self._min_jf_day = _to_float(config.get("min_jf_day"), 0)
            self._max_seeders = _to_int(config.get("max_seeders"), 5)
            self._cron = str(config.get("cron") or "").strip()
        if self._onlyonce:
            self._onlyonce = False
            self.update_config({
                "enabled": self._enabled, "notify": self._notify, "onlyonce": False,
                "site_id": self._site_id, "domain": self._domain, "cookie": self._cookie,
                "ua": self._ua, "downloader": self._downloader, "save_path": self._save_path,
                "qb_category": self._qb_category, "max_count": self._max_count,
                "max_size_gb": self._max_size_gb, "min_jf_day": self._min_jf_day,
                "max_seeders": self._max_seeders, "cron": self._cron,
            })
            logger.info("HHanClub 保种助手：立即运行一次")
            try:
                self._run()
            except Exception as err:
                # 立即运行失败不能影响配置保存（否则前端报 500）
                logger.error(f"HHanClub 保种助手：立即运行出错：{err}")

    def get_state(self) -> bool:
        return bool(self._enabled)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    @staticmethod
    def get_api() -> List[Dict[str, Any]]:
        return []

    @staticmethod
    def get_form() -> Tuple[List[dict], Dict[str, Any]]:
        # 站点下拉选项由 SitesHelper 提供（与 brushflow 相同做法）
        try:
            site_options = [{"title": site.get("name"), "value": site.get("id")}
                            for site in SitesHelper().get_indexers()]
        except Exception as err:
            logger.error(f"获取站点列表失败：{err}")
            site_options = []
        site_options = [{"title": "不关联站点（用下方域名+Cookie）", "value": 0}] + site_options
        # 下载器下拉：MoviePilot 已配置下载器的【名称】（get_service 按名称取，不是类型）
        try:
            dl_options = [{"title": name, "value": name}
                          for name in DownloaderHelper().get_configs().keys()]
        except Exception as err:
            logger.error(f"获取下载器列表失败：{err}")
            dl_options = []
        if not dl_options:
            dl_options = [
                {"title": "Qbittorrent", "value": "qbittorrent"},
                {"title": "Transmission", "value": "transmission"},
            ]
        return [
            {
                'component': 'VForm',
                'content': [
                    # 第一行：三个开关
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'enabled', 'label': '启用插件'},
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'notify', 'label': '发送通知'},
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'onlyonce', 'label': '立即运行一次'},
                                }]
                            },
                        ]
                    },
                    # 站点 + 下载器
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'site_id',
                                        'label': 'HHanClub 站点（读取站点 Cookie）',
                                        'items': site_options,
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'downloader',
                                        'label': '下载器（设定→下载器里配置的名称）',
                                        'items': dl_options,
                                    },
                                }]
                            },
                        ]
                    },
                    # 手动域名 + Cookie（不关联站点时用）
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'domain',
                                        'label': '站点域名（选了站点可留空）',
                                        'placeholder': 'https://hhanclub.net',
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cookie',
                                        'label': 'Cookie（选了站点可留空）',
                                        'placeholder': 'c_secure_uid=...; c_secure_pass=...',
                                    },
                                }]
                            },
                        ]
                    },
                    # 保存路径 / 分类 / UA
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'save_path',
                                        'label': '保存路径（留空用下载器默认）',
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'qb_category',
                                        'label': 'qBittorrent 分类（可选）',
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'ua',
                                        'label': 'UA（留空用浏览器 UA）',
                                        'placeholder': 'Mozilla/5.0 ...',
                                    },
                                }]
                            },
                        ]
                    },
                    # 数量 / 单种子体积 / 收益门槛 / 做种人数上限
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'max_count',
                                        'label': '单次最多下载（个）',
                                        'placeholder': '3',
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'max_size_gb',
                                        'label': '单种子体积上限（GB，0 不限）',
                                        'placeholder': '200',
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'min_jf_day',
                                        'label': '积分/天 净增量门槛（默认0）',
                                        'placeholder': '0',
                                    },
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'max_seeders',
                                        'label': '做种人数上限（默认 5）',
                                        'placeholder': '5',
                                    },
                                }]
                            },
                        ]
                    },
                    # 定时
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '定时（Cron，默认 14:10）',
                                        'placeholder': '10 14 * * *',
                                    },
                                }]
                            },
                        ]
                    },
                    # 说明文字
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VAlert',
                                    'props': {
                                        'type': 'info',
                                        'variant': 'tonal',
                                        'text': '做种积分只有两项：①基础项=mybonus.php「基本奖励」行基础憨豆'
                                                '（=0.02×做种数+B20(A池)，无加成上限50）；'
                                                '②保种区额外=档位倍率×0.25×A_i×(当日做种小时/24)，'
                                                '满24h做种即 档位×0.25×A_i。'
                                                '排名值=①的净增量+②的满时值（实际结算按当日做种时长折算，'
                                                '需每天≥18h达标）。A池自动读 mybonus.php，无需手填。'
                                                '档位(下载时人数锁定)：≤1人→2×，2-3人→1.75×，4-5人→1.5×。'
                                                '模型已用 2026-09-14 真实结算（2种子/205积分/221憨豆/18h）'
                                                '逐分反推验证。',
                                    },
                                }]
                            },
                        ]
                    },
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "site_id": 0,
            "domain": "",
            "cookie": "",
            "ua": "",
            "downloader": "",
            "save_path": "",
            "qb_category": "",
            "max_count": 3,
            "max_size_gb": 0,
            "min_jf_day": 0,
            "max_seeders": 5,
            "cron": "10 14 * * *",
        }

    @staticmethod
    def get_page() -> Optional[List[dict]]:
        # 详情页展示最近一次运行的结果（数据由 _run 保存）
        try:
            last = HHanRescue().get_data("last_result")
        except Exception:
            last = None
        if not last:
            return [
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'text': '暂无运行结果，请先在配置页启用并立即运行一次',
                    },
                }
            ]
        head = last.get("time", "")
        rows = last.get("rows", []) if isinstance(last, dict) else []
        elements = [{
            'component': 'VAlert',
            'props': {
                'type': 'info',
                'variant': 'tonal',
                'text': f"最近运行：{head} · 池 A = {last.get('pool_a', 0)} · "
                        f"共 {last.get('total', 0)} 个种子（展示前 20）",
            },
        }]
        for r in rows[:20]:
            elements.append({
                'component': 'VListItem',
                'props': {
                    'title': f"[{r.get('id')}] {r.get('title', '')[:60]}",
                    'subtitle': f"{r.get('size_gb', 0)}GB · {r.get('seeders')}人做种 · "
                                f"积分 +{r.get('jf_day', 0):.1f}/天"
                                f"（保种区 {r.get('rescue_day', 0):.1f} + 基础项 {r.get('base_day', 0):.1f}）"
                                + (" · 已推送下载" if r.get('downloaded') else ""),
                },
            })
        return [{
            'component': 'VList',
            'props': {'lines': 'three'},
            'content': elements,
        }]

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时服务：CronTrigger 优先，无效则回退每天 14:10"""
        if not self.get_state():
            return []
        if self._cron:
            try:
                trigger: Union[str, CronTrigger] = CronTrigger.from_crontab(self._cron)
                kwargs: Dict[str, Any] = {}
            except ValueError as err:
                logger.error(f"HHanClub 保种助手 CRON 无效：{err}，回退默认 10 14 * * *")
                trigger = CronTrigger(hour=14, minute=10)
                kwargs = {}
        else:
            trigger = CronTrigger(hour=14, minute=10)
            kwargs = {}
        return [
            {
                "id": "HHanRescue",
                "name": "HHanClub 保种区收益计算",
                "trigger": trigger,
                "func": self._run,
                "kwargs": kwargs,
            }
        ]

    def stop_service(self):
        pass

    # ------------------------------------------------------------------
    # 站点访问
    # ------------------------------------------------------------------

    def _site_context(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """解析站点访问上下文： (base_url, cookie, ua)"""
        ua = self._ua or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
        # 优先取 MoviePilot 站点配置里的 Cookie
        if self._site_id:
            try:
                sid = int(self._site_id)
                if sid > 0:
                    site = self._siteoper.get(sid)
                    if site and site.domain:
                        cookie = site.cookie or ""
                        domain = site.domain
                        if site.ua:
                            ua = site.ua
                        return f"https://{domain}", cookie, ua
            except (TypeError, ValueError) as err:
                logger.warning(f"HHanClub 保种助手：站点 ID 无效：{err}")
        domain = self._domain or "hhanclub.net"
        return f"https://{domain}", self._cookie, ua

    def _fetch(self, url: str) -> Optional[str]:
        """带 Cookie 抓取页面文本"""
        base_url, cookie, ua = self._site_context()
        if not cookie:
            logger.error("HHanClub 保种助手：未配置 Cookie（站点未选或 Cookie 为空）")
            return None
        if url.startswith("/"):
            url = base_url + url
        res = RequestUtils(cookies=cookie, ua=ua).get_res(url)
        if res and res.ok:
            return res.text
        logger.error(f"HHanClub 保种助手：抓取失败 {url} status={getattr(res, 'status_code', None)}")
        return None

    # ------------------------------------------------------------------
    # 解析与模型
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_size(text: str) -> float:
        """'189.28 GB' / '1.5 TB' / '512 MB' → GB"""
        m = re.search(r"([\d.]+)\s*(TB|GB|MB|KB)", text, re.I)
        if not m:
            return 0.0
        val = float(m.group(1))
        unit = m.group(2).upper()
        if unit == "TB":
            return val * 1024
        if unit == "MB":
            return val / 1024
        if unit == "KB":
            return val / 1024 / 1024
        return val

    @staticmethod
    def _parse_datetime(text: str) -> Optional[datetime]:
        """'2026-02-03 09:20:46' → datetime（失败返回 None）"""
        text = (text or "").strip()
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
        if not m:
            return None
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6) or 0),
            )
        except ValueError:
            return None

    @staticmethod
    def _parse_rescue_page(html: str) -> List[Dict[str, Any]]:
        """解析 rescue.php 的种子卡片（已对照 2026-09 实际页面结构验证）

        页面不是 <table>，而是 div 卡片流。每张卡片：
        <div class="... torrent-table-sub-info">
          ... <a href='details.php?id=NNN&hit=1' class='... torrent-info-text-name'>标题</a>
          <div class="torrent-info-text torrent-info-text-size">189.28 GB</div>
          <div class="torrent-info-text torrent-info-text-added">2026-02-03 09:20:46</div>
          <div class="torrent-info-text torrent-info-text-seeders"><a ...>5</a></div>
        """
        rows: List[Dict[str, Any]] = []
        if not html:
            return rows
        # 1. id + 标题（标题锚点 class 引号单双兼容）
        for m in re.finditer(
                r"details\.php\?id=(\d+)&(?:amp;)?hit=1[\"'][^>]*torrent-info-text-name[\"']>([^<]+)</a>",
                html):
            rows.append({
                "id": int(m.group(1)),
                "title": m.group(2).strip()[:80],
            })
        # 2. 按 (size, added, seeders) 三元组顺序补全——卡片内顺序固定：体积→时间→做种
        #    （class 属性的引号单双都出现过，["'] 兼容两种）
        sizes = [HHanRescue._parse_size(x) for x in re.findall(
            r"torrent-info-text-size[\"']>\s*([^<]+?)\s*<", html)]
        addeds = [HHanRescue._parse_datetime(x) for x in re.findall(
            r"torrent-info-text-added[\"']>\s*([^<]+?)\s*<", html)]
        seeders = [int(x) for x in re.findall(
            r"torrent-info-text-seeders[\"']>\s*(?:<a[^>]*>|<span[^>]*>)(\d+)</", html)]
        if not (len(rows) == len(sizes) == len(addeds) == len(seeders)):
            logger.warning(f"HHanClub 保种助手：字段数不一致 id={len(rows)} size={len(sizes)} "
                           f"added={len(addeds)} seeders={len(seeders)}，页面结构可能变化")
        for i, row in enumerate(rows):
            if i < len(sizes):
                row["size_gb"] = sizes[i]
            if i < len(addeds):
                row["added"] = addeds[i]
            if i < len(seeders):
                row["seeders"] = seeders[i]
        return rows

    def _fetch_pool_a(self) -> float:
        """抓 mybonus.php「基本奖励」行的 A 值 = 你的全站做种池 A

        基础项曲线 B20 的自变量就是它（0.02×做种数+B20(A)=页面基础项速率，
        已对拍 41.268 精确成立）。读取失败返回 0——排名退化为只按保种区额外项。
        """
        html = self._fetch(MYBONUS_URL)
        if not html:
            logger.warning("HHanClub 保种助手：抓取 mybonus.php 失败，基础项增量按 0 处理")
            return 0.0
        # bonus-table「基本奖励」行的 A 值（带千分位逗号，如 2,179.711）
        m = re.search(
            r"基本奖励(?:</div>|[^<]*)*\s*<div>[^<]*</div>\s*<div>[^<]*</div>\s*<div>\s*([\d,.]+)\s*</div>",
            html)
        pool_a = 0.0
        if m:
            try:
                pool_a = float(m.group(1).replace(",", ""))
            except ValueError:
                logger.warning(f"HHanClub 保种助手：A 值解析失败 [{m.group(1)}]")
        else:
            # 兜底：页面顶部「你当前每小时能获取N个积分 (A = 6238)」
            m2 = re.search(r"\(A\s*=\s*([\d.]+)\)", html)
            if m2:
                try:
                    pool_a = float(m2.group(1))
                except ValueError:
                    pass
        if pool_a <= 0:
            logger.warning("HHanClub 保种助手：未读到做种池 A，基础项增量按 0 处理")
            return 0.0
        # 自检：0.02×做种数+B20(A) 应≈页面顶部「每小时能获取 N 个积分」
        m3 = re.search(r"每小时能获取\s*([\d.]+)\s*个积分", html)
        if m3:
            try:
                rate = float(m3.group(1))
                logger.info(f"HHanClub 保种助手：池 A = {pool_a:.1f}，页面基础项速率 = {rate:.3f}/h")
            except ValueError:
                pass
        return pool_a

    # ------------------------------------------------------------------
    # 收益计算
    # ------------------------------------------------------------------

    def _rank(self, torrents: List[Dict[str, Any]], pool_a: float) -> List[Dict[str, Any]]:
        """按「下载后总做种积分净增量/天（满时口径）」排序

        做种积分只有两项（wiki 原文，经 2026-09-14 真实结算反推验证）：
          ①基础项 = 0.02×做种数 + B20(A池)（每小时；与保种区无关）
             下载本种子后的净增量 = [0.02 + B20(池+i) − B20(池)] × 24
          ②保种区额外 = 档位倍率 × 0.25 × A_i × (当日做种小时/24)
             排名按满 24h 口径 = 档位倍率 × 0.25 × A_i（实际结算按当日时长折算）
          排名值 = ①+②，直接降序。
        """
        now = datetime.now()
        b_old = base_rate_b(pool_a) if pool_a > 0 else 0.0
        results = []
        for t in torrents:
            gb = t.get("size_gb") or 0
            seeders = int(t.get("seeders") or 0)
            added = t.get("added")
            if gb <= 0:
                continue
            if seeders > self._max_seeders:
                continue
            weeks = 0.0
            if added:
                delta = now - added
                weeks = max(delta.days, 0) / 7.0
            a_i = calc_a(gb, weeks, seeders)
            jf_m = tier_of(seeders)
            # ①基础项净增量（池 A 读取失败时按 0，只按②排名）
            if pool_a > 0:
                base_day = (COUNT_RATE + base_rate_b(pool_a + a_i) - b_old) * 24
            else:
                base_day = 0.0
            # ②保种区额外（满时口径）
            rescue_day = jf_m * RESCUE_DAILY_C * a_i
            results.append({
                **t,
                "A": round(a_i, 2),
                "weeks": round(weeks, 1),
                "base_day": round(base_day, 2),
                "rescue_day": round(rescue_day, 2),
                "jf_day": round(base_day + rescue_day, 2),
                "downloaded": False,
            })
        results.sort(key=lambda x: x["jf_day"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # 下载推送
    # ------------------------------------------------------------------

    def _service_info(self) -> Optional[ServiceInfo]:
        """获取配置的下载器服务

        DownloaderHelper.get_service(name=...) 按下载器【名称】匹配（设定→下载器里
        自己起的名字，如 "qb" / "本地qB"），不是按类型 qbittorrent/transmission。
        名称取不到时按类型兜底匹配，兼容旧配置里直接填类型名的情况。
        """
        if not self._downloader:
            logger.error("HHanClub 保种助手：未配置下载器")
            return None
        helper = DownloaderHelper()
        service = helper.get_service(name=self._downloader)
        if not service:
            # 按类型兜底：旧配置可能填的是 qbittorrent/transmission
            service = helper.get_service(type_filter=self._downloader)
        if not service:
            logger.error(f"HHanClub 保种助手：获取下载器 [{self._downloader}] 失败，"
                         f"请在插件里选择「设定→下载器」中配置的名称")
            return None
        if service.instance.is_inactive():
            logger.error(f"HHanClub 保种助手：下载器 [{self._downloader}] 未连接")
            return None
        return service

    def _download_torrent(self, torrent_id: int, title: str) -> Optional[str]:
        """把单个种子推送到下载器，返回种子 hash

        直接调下载器实例的 add_torrent（与 brushflow 插件同做法）：
        - qB：先加一个随机 Tag，添加成功后用 get_torrent_id_by_tag 回查 hash
          （MP 侧会重试 10 次×3 秒，查到后自动删掉随机 Tag）
        - TR：add_torrent 直接返回 Torrent 对象，取 hashString
        插件自己的标签（PLUGIN_TAG）随后统一补打。
        """
        service = self._service_info()
        if not service:
            return None
        helper = DownloaderHelper()
        downloader = service.instance
        base_url, cookie, ua = self._site_context()
        dl_url = DOWNLOAD_URL.format(id=torrent_id)
        res = RequestUtils(cookies=cookie, ua=ua).get_res(dl_url)
        content = None
        if res and res.ok:
            content = res.content
        if not content:
            logger.error(f"HHanClub 保种助手：下载种子文件失败 id={torrent_id}")
            return None
        torrent_hash: Optional[str] = None
        try:
            if helper.is_downloader("qbittorrent", service=service):
                random_tag = StringUtils.generate_random_str(10)
                ok, _ids = downloader.add_torrent(
                    content=content,
                    download_dir=self._save_path or None,
                    cookie=cookie,
                    category=self._qb_category or None,
                    tag=[PLUGIN_TAG, random_tag],
                )
                if not ok:
                    return None
                # 随机 Tag 回查 hash（MP 内部重试；查到即删随机 Tag）
                torrent_hash = downloader.get_torrent_id_by_tag(tags=random_tag)
            elif helper.is_downloader("transmission", service=service):
                added = downloader.add_torrent(
                    content=content,
                    download_dir=self._save_path or None,
                    cookie=cookie,
                    labels=[PLUGIN_TAG],
                )
                torrent_hash = added.hashString if added else None
            else:
                logger.error(f"HHanClub 保种助手：不支持的下载器类型 [{service.type}]")
                return None
        except Exception as err:
            logger.error(f"HHanClub 保种助手：推送 id={torrent_id} 出错：{err}")
            return None
        # 补打插件标签（qB 随机 Tag 已删；这里统一确保 PLUGIN_TAG 在）
        if torrent_hash:
            try:
                if helper.is_downloader("qbittorrent", service=service):
                    downloader.set_torrents_tag(ids=torrent_hash, tags=[PLUGIN_TAG])
                else:
                    downloader.set_torrent_tag(ids=torrent_hash, tags=[PLUGIN_TAG])
            except Exception as err:
                logger.warning(f"HHanClub 保种助手：打标签失败 id={torrent_id}：{err}")
        return torrent_hash

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def _run(self):
        """抓取 → 排序 → 通知/下载"""
        logger.info("HHanClub 保种助手：开始运行")
        html = self._fetch(RESCUE_URL)
        if not html:
            self._notify_message("HHanClub 保种助手运行失败", "抓取保种区页面失败，请检查 Cookie")
            return
        torrents = self._parse_rescue_page(html)
        logger.info(f"HHanClub 保种助手：解析到 {len(torrents)} 个种子")
        if not torrents:
            self._notify_message("HHanClub 保种助手", "保种区解析到 0 个种子（页面结构可能变化）")
            return
        pool_a = self._fetch_pool_a()
        results = self._rank(torrents, pool_a)
        # 过滤并取 TopN
        picked = []
        skipped: List[str] = []
        for r in results:
            if len(picked) >= self._max_count:
                break
            if r["jf_day"] <= 0:
                continue
            if self._min_jf_day and r["jf_day"] < self._min_jf_day:
                continue
            if self._max_size_gb and r["size_gb"] > self._max_size_gb:
                continue
            picked.append(r)
        if not picked:
            self._notify_message("HHanClub 保种助手",
                                 f"本轮没有符合门槛的种子（共 {len(results)} 个，"
                                 f"积分门槛 {self._min_jf_day}/天）")
            return
        # 推送下载
        for r in picked:
            try:
                torrent_hash = self._download_torrent(r["id"], r["title"])
                r["downloaded"] = bool(torrent_hash)
                r["hash"] = torrent_hash
                if torrent_hash:
                    logger.info(f"HHanClub 保种助手：已推送 id={r['id']} hash={torrent_hash}")
                else:
                    skipped.append(f"{r['id']} {r['title'][:30]}")
            except Exception as err:
                logger.error(f"HHanClub 保种助手：推送 id={r['id']} 失败：{err}")
                skipped.append(f"{r['id']} {r['title'][:30]}")
        # 保存结果（详情页展示）——注意剔除 datetime 字段，plugindata 的 value 列
        # 是 JSON 类型，SQLAlchemy 序列化不认 datetime（会报 "Object of type
        # datetime is not JSON serializable" 并中断整个 _run）
        self.save_data("last_result", {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pool_a": round(pool_a, 1),
            "total": len(results),
            "rows": [{
                "id": r.get("id"), "title": r.get("title"), "size_gb": r.get("size_gb"),
                "seeders": r.get("seeders"), "A": r.get("A"), "weeks": r.get("weeks"),
                "base_day": r.get("base_day"), "rescue_day": r.get("rescue_day"),
                "jf_day": r.get("jf_day"),
                "downloaded": r.get("downloaded"), "hash": r.get("hash"),
            } for r in results[:20]],
        })
        # 通知
        if self._notify:
            lines = [f"池 A = {pool_a:.0f}，共 {len(results)} 个种子，"
                     f"正收益 {sum(1 for r in results if r['jf_day'] > 0)} 个，"
                     f"本轮挑选 {len(picked)} 个：", ""]
            for r in picked:
                lines.append(f"· [{r['id']}] {r['title'][:40]}")
                lines.append(f"  {r['size_gb']}GB · {r['seeders']}人做种 · "
                             f"积分 +{r['jf_day']:.1f}/天"
                             f"（保种区 {r['rescue_day']:.1f} + 基础项 {r['base_day']:.1f}）· A={r['A']:.0f}"
                             + (" · 已推送" if r["downloaded"] else " · 推送失败"))
            if skipped:
                lines.append("")
                lines.append("推送失败：" + "、".join(skipped))
            self._notify_message("HHanClub 保种助手运行结果", "\n".join(lines))

    def _notify_message(self, title: str, text: str):
        if self._notify:
            self.post_message(mtype=NotificationType.Plugin, title=title, text=text)
        logger.info(f"{title}: {text[:200]}")
