"""由人工标注表展开出 queries.jsonl。

为什么不直接手写 80 行 JSON：qid 连号、chunk_id 拼接、类型/场景配额都是机械劳动，
手写容易出现「分布看着对、实际 79 条」或 law_code 拼错这类不可见错误。把语义部分
（问法文本、gold 指向哪条法条）留在下面的标注表里由人写，脚本只做确定性展开与
硬校验，任何一条不合规就拒绝生成。

标注表里每一条 (law, article) 都来自阶段 1 语料的真实正文：本文件不生成语义、
不调用任何模型，也不引用未读过的条文。校验与复核证据见 validate.py。

辅助 gold 的选取规则（收紧过一次，原因见下）：只有当 query **明确问到了第二个
法律争点**、缺了这条答案就不完整时才挂。曾经把「相邻条文/上位条文/处罚后果」
也挂进 gold，抽样复核时发现它们属于「相关但答非所问」，会让 gold 正确率掺水，
也会让 recall@k 把检索到噪声条文算成正确。现在 80 条里 72 条单 gold，只有 8 条
是真正一问多答的 2–3 gold。

用法（仓库根执行）：
    uv run --project python-agent python lab/dataset/build_dataset.py
"""

# 生成结果输出即交付物（要贴进报告），CLI 打印是意图而非疏漏；全局 T20 规则在此豁免。
# ruff: noqa: T201

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python-agent"))

from retrieval.chunker import LAW_CODES  # noqa: E402

OUT_FILE = Path(__file__).resolve().parent / "queries.jsonl"

SCENES = ("subject", "commercial", "liability", "ip", "data", "compliance")
TYPES = ("concept", "scene", "colloquial")
# 40% / 30% / 30%；与每场景「4 组三问法 + 1~2 组仅概念型」的展开结果逐项对齐。
EXPECTED_DIST = {"concept": 32, "scene": 24, "colloquial": 24}
EXPECTED_TOTAL = 80
MIN_PER_SCENE = 10

# 每条 gold 统一挂 S1_whole 的 idx=0：整条法条必产出一个且仅一个 chunk，
# 用这个最稳定的 id 作锚点；命中判定另按 article 比对，不受策略影响。
STRATEGY = "S1_whole"
IDX = 0


@dataclass(frozen=True, slots=True)
class Annotation:
    scene: str
    primary: tuple[str, str]  # (law, article)，中文法律名与中文条号
    queries: dict[str, str]  # 问法类型 -> 人写的查询文本
    # 只在 query 真的问到第二个争点时才填，格式：问法类型 -> 追加的 (law, article)。
    gold_extra: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)


ANNOTATIONS: tuple[Annotation, ...] = (
    # ---------------- subject：谁能签、谁担责、以谁的名义 ----------------
    Annotation(
        scene="subject",
        primary=("民法典", "第十八条"),
        # 第十八条同时给出「成年」与「16 周岁以自己劳动收入为主要生活来源」两个口径，
        # 所以场景问法才设计成 17 岁实习生，而不是随便一个未成年人。
        queries={
            "concept": "完全民事行为能力人的认定标准是什么",
            "scene": "合作方派来的签约人十七周岁、靠自己的工资生活，他能独立签这份采购合同吗",
            "colloquial": "多大岁数才能自己签合同啊",
        },
    ),
    Annotation(
        scene="subject",
        primary=("民法典", "第五十七条"),
        queries={
            "concept": "法人独立享有民事权利和承担民事义务的能力如何界定",
            "scene": "供应商是家有限责任公司，签约时我方应当以谁的名义主张合同权利",
            "colloquial": "公司算不算自己能担责的人",
        },
    ),
    Annotation(
        scene="subject",
        primary=("民法典", "第六十一条"),
        # 法定代表人变更不影响既有合同，是第六十一条「法律后果由法人承受」的直接推论。
        queries={
            "concept": "法定代表人以法人名义从事民事活动的法律后果由谁承受",
            "scene": "对方公司换了法定代表人，前任老总签的框架协议还对公司有效吗",
            "colloquial": "老板签的字公司得认吗",
        },
    ),
    Annotation(
        scene="subject",
        primary=("公司法", "第二十三条"),
        queries={
            "concept": "股东滥用公司法人独立地位逃避债务时应当承担什么责任",
            "scene": "债务人是一人公司，股东把公司账户和私人账户混着用，能要求股东一起还钱吗",
            "colloquial": "公司没钱了能不能找股东要",
        },
    ),
    Annotation(
        scene="subject",
        primary=("保险法", "第十二条"),
        # 第十二条区分人身险「订立时」与财产险「事故发生时」的保险利益时点，
        # 概念问法必须能唯一落回这一条，故只给概念型。
        queries={"concept": "人身保险与财产保险对保险利益的时间要求有何不同"},
    ),
    # ---------------- commercial：条款怎么写、怎么履行、怎么解除 ----------------
    Annotation(
        scene="commercial",
        primary=("民法典", "第四百七十条"),
        queries={
            "concept": "合同的内容一般应当包括哪些条款",
            "scene": "我们要拟一份设备采购合同，法务要求必备条款写全，具体得列哪几项",
            "colloquial": "合同里都得写点啥才算齐",
        },
    ),
    Annotation(
        scene="commercial",
        primary=("民法典", "第四百九十六条"),
        # 场景问法同时问了「没提示」与「排除主要权利」两个争点，分别对应
        # 第四百九十六条（不成为合同内容）与第四百九十七条（无效），故给 2 个 gold。
        queries={
            "concept": "提供格式条款的一方对免除或者减轻其责任的条款负有什么义务",
            "scene": (
                "平台把免责条款藏在折叠协议里没有提示，"
                "条款里还排除了用户主要权利，这两类条款分别怎么处理"
            ),
            "colloquial": "格式合同里藏着的免责条款算数吗",
        },
        gold_extra={"scene": (("民法典", "第四百九十七条"),)},
    ),
    Annotation(
        scene="commercial",
        primary=("民法典", "第四百九十七条"),
        queries={
            "concept": "哪些情形下的格式条款无效",
            "scene": "合同约定无论任何原因概不退款并排除消费者主要权利，这一条效力如何",
            "colloquial": "霸王条款是不是一律不作数",
        },
    ),
    Annotation(
        scene="commercial",
        primary=("民法典", "第五百零九条"),
        queries={
            "concept": "合同履行中的通知协助保密等附随义务是如何规定的",
            "scene": "卖方交货后拒绝提供安装必需的配合与资料，是否违反诚信原则下的协助义务",
            "colloquial": "合同没写的事儿对方就能不管吗",
        },
    ),
    Annotation(
        scene="commercial",
        primary=("民法典", "第五百六十三条"),
        queries={"concept": "当事人可以法定解除合同的情形有哪些"},
    ),
    Annotation(
        scene="commercial",
        primary=("民法典", "第五百一十一条"),
        # 第五百一十一条开头是「依据前条规定仍不能确定的」，缺了第五百一十条
        # 的「先协议补充」这一步，答案链条不完整。
        queries={"concept": "合同对价款或者履行地点约定不明确时应当如何确定"},
        gold_extra={"concept": (("民法典", "第五百一十条"),)},
    ),
    # ---------------- liability：违约之后赔多少、免不免 ----------------
    Annotation(
        scene="liability",
        primary=("民法典", "第五百七十七条"),
        queries={
            "concept": "违约方应当承担继续履行采取补救措施或者赔偿损失等哪些责任形式",
            "scene": "软件开发商交付的系统跑不通，我方要求继续修复并赔偿返工损失有没有依据",
            "colloquial": "对方不按合同办事能怎么办",
        },
    ),
    Annotation(
        scene="liability",
        primary=("民法典", "第五百八十四条"),
        # 第五百八十四条含可得利益与可预见规则两层，场景问法用「转售订单」逼出
        # 「合同履行后可以获得的利益」这一表述，口语问法只留最直接的少赚的钱。
        queries={
            "concept": "违约损失赔偿额的范围如何界定，是否包括合同履行后可以获得的利益",
            "scene": "供应商断供导致我方丢了一笔转售订单，主张预期利润能否获得支持",
            "colloquial": "对方违约我少赚的钱能要回来吗",
        },
    ),
    Annotation(
        scene="liability",
        primary=("民法典", "第五百八十五条"),
        # 「实际损失很小」是调减的判断基准，基准本身写在第五百八十四条，
        # 因此场景问法需要两条共同组成答案。
        queries={
            "concept": "约定的违约金过分高于造成的损失时当事人可以请求如何调整",
            "scene": (
                "采购合同约定逾期每日按合同总额百分之五付违约金，实际损失很小，法院会支持调减吗"
            ),
            "colloquial": "违约金写太高了能不能少赔点",
        },
        gold_extra={"scene": (("民法典", "第五百八十四条"),)},
    ),
    Annotation(
        scene="liability",
        primary=("民法典", "第五百八十六条"),
        queries={
            "concept": "定金数额的上限是如何规定的",
            "scene": "总价一百万的合同收了三十万定金，超出部分有没有定金效力",
            "colloquial": "定金最多能收多少",
        },
    ),
    Annotation(
        scene="liability",
        primary=("民法典", "第五百八十八条"),
        # 择一适用必然牵出「被择的两条」本身，故给足 3 个 gold。
        queries={"concept": "同时约定违约金和定金时守约方应当如何主张"},
        gold_extra={"concept": (("民法典", "第五百八十五条"), ("民法典", "第五百八十七条"))},
    ),
    Annotation(
        scene="liability",
        primary=("民法典", "第五百九十条"),
        queries={"concept": "因不可抗力不能履行合同时责任免除与通知义务如何规定"},
    ),
    # ---------------- ip：语料无专门知识产权法，用技术合同/商业秘密/竞业条覆盖 ----------------
    Annotation(
        scene="ip",
        primary=("民法典", "第一百二十三条"),
        # 第一百二十三条的客体清单里明确列了「商业秘密」，场景问法据此设计。
        queries={
            "concept": "民法典规定的知识产权客体包括哪些",
            "scene": "客户把自研算法和源码当商业秘密要求写进保密清单，属于法定权利客体吗",
            "colloquial": "法律保护的知识产权都有啥",
        },
    ),
    Annotation(
        scene="ip",
        primary=("民法典", "第八百四十七条"),
        # 「用公司设备和项目时间」对应职务成果定义；第八百四十八条给出非职务
        # 成果归属，是判断「算不算职务」必需的反面标准。
        queries={
            "concept": "职务技术成果的使用权与转让权归属于谁",
            "scene": "员工用公司设备和项目时间做出一套算法，公司能否直接对外签技术转让合同",
            "colloquial": "上班做的发明算谁的",
        },
        gold_extra={"scene": (("民法典", "第八百四十八条"),)},
    ),
    Annotation(
        scene="ip",
        primary=("民法典", "第八百五十九条"),
        queries={
            "concept": "委托开发完成的发明创造申请专利的权利属于哪一方",
            "scene": "我们出资委托研究所开发新装置且没约定成果归属，专利权归谁、我方能不能实施",
            "colloquial": "花钱请人做发明，专利算谁的",
        },
    ),
    Annotation(
        scene="ip",
        primary=("反不正当竞争法", "第九条"),
        queries={
            "concept": "侵犯商业秘密的行为类型与商业秘密的构成要件是什么",
            "scene": "前员工把公司客户报价表带到新东家使用，是否构成侵犯商业秘密",
            "colloquial": "把老东家的客户名单带走算违法吗",
        },
    ),
    Annotation(
        scene="ip",
        primary=("劳动合同法", "第二十四条"),
        queries={"concept": "竞业限制的人员范围与最长期限是如何规定的"},
    ),
    # ---------------- data：个人信息定义、处理、权利、安全、电子数据 ----------------
    Annotation(
        scene="data",
        primary=("民法典", "第一千零三十四条"),
        # 第一千零三十四条第二款逐项列举身份证号、健康信息、行踪信息，
        # 场景问法直接照这三个例子写，避免出现语料里没有的信息类型。
        queries={
            "concept": "个人信息在法律上如何定义并包含哪些类型",
            "scene": "应用收集用户身份证件号码行踪信息和健康数据，这些是否属于个人信息",
            "colloquial": "啥算个人信息啊",
        },
    ),
    Annotation(
        scene="data",
        primary=("民法典", "第一千零三十五条"),
        queries={
            "concept": "处理个人信息应当遵循哪些原则并符合哪些条件",
            "scene": "合作方要把共享的用户数据用于合同约定之外的新用途，需要重新满足什么条件",
            "colloquial": "拿用户信息用之前得满足啥条件",
        },
    ),
    Annotation(
        scene="data",
        primary=("民法典", "第一千零三十七条"),
        # 场景问法同时问「更正/删除」与「泄露后的义务」，后者在第一千零三十八条。
        queries={
            "concept": "自然人对其个人信息享有哪些查阅复制更正与删除的权利",
            "scene": (
                "用户发现平台留的手机号是错的，要求更正并在注销后彻底删档；"
                "平台还把信息泄露了，各有什么责任"
            ),
            "colloquial": "我能要求把我的信息删掉吗",
        },
        gold_extra={"scene": (("民法典", "第一千零三十八条"),)},
    ),
    Annotation(
        scene="data",
        primary=("民法典", "第一千零三十八条"),
        # 第二款明确要求「及时采取补救措施、告知自然人并向主管部门报告」，
        # 场景问法把三项义务全部反着写，检验检索能否定位到这一款。
        queries={
            "concept": "信息处理者对个人信息负有哪些安全保护与泄露告知义务",
            "scene": "系统被拖库后我方打算内部悄悄修复，不通知用户也不上报主管部门是否合规",
            "colloquial": "数据泄露了要不要告诉用户",
        },
    ),
    Annotation(
        scene="data",
        primary=("民事诉讼法", "第六十六条"),
        queries={"concept": "民事诉讼法规定的证据种类中电子数据处于什么地位"},
    ),
    # ---------------- compliance：广告、披露、宣传的合规红线 ----------------
    Annotation(
        scene="compliance",
        primary=("广告法", "第四条"),
        # 「全网销量第一」同时踩第四条真实性、第九条禁用最高级用语、
        # 第二十八条虚假广告，三条一起才能给出完整合规结论。
        queries={
            "concept": "广告内容的真实性由谁负责且法律禁止哪些内容",
            "scene": "市场部要在落地页写全网销量第一但没有权威统计支撑，能不能发布",
            "colloquial": "广告能随便吹吗",
        },
        gold_extra={"scene": (("广告法", "第九条"), ("广告法", "第二十八条"))},
    ),
    Annotation(
        scene="compliance",
        primary=("广告法", "第二十八条"),
        queries={
            "concept": "构成虚假广告的法定情形有哪些",
            "scene": "招商页引用了一份无法验证的行业调查报告来证明效果，是否属于虚假广告",
            "colloquial": "哪些广告算虚假广告",
        },
    ),
    Annotation(
        scene="compliance",
        primary=("广告法", "第四十六条"),
        queries={
            "concept": "哪些广告在发布前必须由广告审查机关进行审查",
            "scene": "客户要投一组保健食品和医疗器械广告，还没送审就要求上线可以吗",
            "colloquial": "保健品的广告能直接发吗",
        },
    ),
    Annotation(
        scene="compliance",
        primary=("证券法", "第七十八条"),
        # 场景问法追加了「公告有重大遗漏会有什么后果」，对应第八十五条的赔偿责任。
        queries={
            "concept": "信息披露义务人披露的信息应当符合哪些要求",
            "scene": (
                "上市公司拟在境外先发一份重大事项公告境内隔天再发，"
                "公告里还有重大遗漏，是否合规、会有什么后果"
            ),
            "colloquial": "上市公司发公告有啥硬要求",
        },
        gold_extra={"scene": (("证券法", "第八十五条"),)},
    ),
    Annotation(
        scene="compliance",
        primary=("反不正当竞争法", "第八条"),
        queries={"concept": "经营者不得实施哪些虚假或者引人误解的商业宣传行为"},
    ),
)


def chunk_id(law: str, article: str) -> str:
    # LAW_CODES 直接复用阶段 1 的登记表，避免标注集与切分器各写一份映射而漂移。
    return f"{LAW_CODES[law]}:{article}:{IDX}:{STRATEGY}"


def build() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for ann in ANNOTATIONS:
        if ann.scene not in SCENES:
            raise ValueError(f"非法场景: {ann.scene}")
        for qtype in TYPES:
            if qtype not in ann.queries:
                continue
            refs = (ann.primary, *ann.gold_extra.get(qtype, ()))
            if not 1 <= len(refs) <= 3:
                raise ValueError(f"gold 数量越界: {ann.primary} {qtype}")
            if len(set(refs)) != len(refs):
                raise ValueError(f"gold 重复: {ann.primary} {qtype}")
            query = ann.queries[qtype].strip()
            if not query:
                raise ValueError(f"空查询: {ann.primary} {qtype}")
            rows.append(
                {
                    "qid": f"q{len(rows) + 1:03d}",
                    "query": query,
                    "gold": [chunk_id(law, article) for law, article in refs],
                    "scene": ann.scene,
                    "type": qtype,
                }
            )

    # 为什么在生成阶段就卡死配额：分布不对会让后续所有实验的裁判集失效，
    # 宁可生成失败，也不要产出一份「看起来差不多」的数据集。
    if len(rows) != EXPECTED_TOTAL:
        raise ValueError(f"总数应为 {EXPECTED_TOTAL}，实际 {len(rows)}")
    for scene in SCENES:
        n = sum(row["scene"] == scene for row in rows)
        if n < MIN_PER_SCENE:
            raise ValueError(f"场景 {scene} 仅 {n} 条，少于 {MIN_PER_SCENE}")
    for qtype, expected in EXPECTED_DIST.items():
        n = sum(row["type"] == qtype for row in rows)
        if n != expected:
            raise ValueError(f"类型 {qtype} 应为 {expected} 条，实际 {n} 条")
    if len({row["query"] for row in rows}) != len(rows):
        raise ValueError("存在重复查询文本")
    return rows


def main() -> int:
    rows = build()
    with OUT_FILE.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"已生成 {OUT_FILE} 共 {len(rows)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
