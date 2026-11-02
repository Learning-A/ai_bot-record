# -*- coding: utf-8 -*-
"""style_switcher 知识库联动逻辑单测（桩掉 astrbot 依赖，验证 _resolve_kb_id/_sync_kb）。"""
import asyncio
import sys
import types


class _KBCreated:
    def __init__(self, kb_id):
        self.kb = types.SimpleNamespace(kb_id=kb_id)


class FakeKBManager:
    def __init__(self, existing=None, fail_create=False):
        self.existing = existing or {}      # name -> kb_id
        self.created = []
        self.fail_create = fail_create

    async def get_kb_by_name(self, name):
        if name in self.existing:
            return _KBCreated(self.existing[name])
        return None

    async def create_kb(self, kb_name, **kw):
        if self.fail_create:
            raise RuntimeError("fake create fail")
        self.created.append(kb_name)
        kb_id = f"kb_{kb_name[:4]}"
        self.existing[kb_name] = kb_id
        return _KBCreated(kb_id)


class FakeSP:
    def __init__(self):
        self.store = {}
        self.writes = 0

    async def get_async(self, scope, scope_id, key, default=None):
        return self.store.get((scope, scope_id, key), default)

    async def put_async(self, scope, scope_id, key, value):
        self.store[(scope, scope_id, key)] = value
        self.writes += 1


# ---- 桩 astrbot ----
fake_api = types.ModuleType("astrbot.api")
fake_api.AstrBotConfig = dict
fake_api.sp = None  # 运行时注入
fake_ev = types.ModuleType("astrbot.api.event")


class _F:
    class PermissionType:
        ADMIN = 1

    class EventMessageType:
        ALL = 1

    def command(self, *a, **k):
        return lambda f: f

    def permission_type(self, *a, **k):
        return lambda f: f

    def on_llm_request(self, *a, **k):
        return lambda f: f


fake_ev.filter = _F()
fake_ev.AstrMessageEvent = object
fake_star = types.ModuleType("astrbot.api.star")
fake_star.Context = object


class _Star:
    def __init__(self, context):
        self.context = context


fake_star.Star = _Star
astrbot = types.ModuleType("astrbot")
core = types.ModuleType("astrbot.core")


class _L:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


core.logger = _L()
fake_prov = types.ModuleType("astrbot.core.provider.entities")
fake_prov.ProviderType = types.SimpleNamespace(TEXT_TO_SPEECH="tts")
astrbot.core = core
for n, m in {
    "astrbot": astrbot, "astrbot.api": fake_api, "astrbot.api.event": fake_ev,
    "astrbot.api.star": fake_star, "astrbot.core": core,
    "astrbot.core.provider": types.ModuleType("astrbot.core.provider"),
    "astrbot.core.provider.entities": fake_prov,
}.items():
    sys.modules[n] = m

sys.path.insert(0, r"F:\sessions\26-10-28直播姬\qq-ai-bot\astrbot\data\plugins\style_switcher")
import main as ss  # noqa: E402

# 注入桩（main 在 import 时已绑定 sp 名字，需直接改模块属性）
fake_sp = FakeSP()
ss.sp = fake_sp


async def run():
    fails = 0

    # 场景1：库不存在 → 自动创建并绑定
    km = FakeKBManager()
    plug = ss.StyleSwitcher(context=None, config={})
    plug.context = types.SimpleNamespace(kb_manager=km)
    await plug._sync_kb("umo1", "paopao")
    ok1 = (km.created == ["泡泡专属知识库"]
           and fake_sp.store.get(("umo", "umo1", "kb_config"), {}).get("kb_ids"))
    print(f"[自动创建+绑定] {'PASS' if ok1 else 'FAIL'} created={km.created} cfg={fake_sp.store.get(('umo','umo1','kb_config'))}")
    fails += 0 if ok1 else 1

    # 场景2：重复同步不再写（幂等）
    w0 = fake_sp.writes
    await plug._sync_kb("umo1", "paopao")
    ok2 = fake_sp.writes == w0
    print(f"[幂等免写] {'PASS' if ok2 else 'FAIL'} writes {w0}->{fake_sp.writes}")
    fails += 0 if ok2 else 1

    # 场景3：切到无映射人格 → 显式空绑定（防全局库泄漏）
    await plug._sync_kb("umo1", "stranger_persona")
    cfg3 = fake_sp.store.get(("umo", "umo1", "kb_config"), {}).get("kb_ids")
    ok3 = cfg3 == []
    print(f"[无映射显式空] {'PASS' if ok3 else 'FAIL'} kb_ids={cfg3}")
    fails += 0 if ok3 else 1

    # 场景4：create 失败不炸、不绑定
    km2 = FakeKBManager(fail_create=True)
    plug2 = ss.StyleSwitcher(context=None, config={})
    plug2.context = types.SimpleNamespace(kb_manager=km2)
    await plug2._sync_kb("umo2", "tangyuan")
    cfg4 = fake_sp.store.get(("umo", "umo2", "kb_config"))
    ok4 = cfg4 is None  # 失败时不写（保留原状）
    print(f"[失败不写] {'PASS' if ok4 else 'FAIL'} cfg={cfg4}")
    fails += 0 if ok4 else 1

    # 场景5：库已存在 → 直接绑定不重建
    km3 = FakeKBManager(existing={"小鱼干专属知识库": "kb_xdd"})
    plug3 = ss.StyleSwitcher(context=None, config={})
    plug3.context = types.SimpleNamespace(kb_manager=km3)
    await plug3._sync_kb("umo3", "default")
    cfg5 = fake_sp.store.get(("umo", "umo3", "kb_config"), {})
    ok5 = cfg5.get("kb_ids") == ["kb_xdd"] and km3.created == []
    print(f"[存量直绑] {'PASS' if ok5 else 'FAIL'} cfg={cfg5} created={km3.created}")
    fails += 0 if ok5 else 1

    print(f"\n=== {'全部通过' if fails == 0 else f'{fails} 项失败'} ===")
    return fails


sys.exit(asyncio.run(run())
         )
