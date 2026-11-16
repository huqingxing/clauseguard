package com.clauseguard.review;

/**
 * 审批快照（{@code review.pending_payload}）不可用，拒绝本次审批。
 *
 * <p>覆盖三种情况，都是「拿不到可信的待审依据」：
 * <ol>
 *   <li>payload 存在但不是合法 JSON（截断、编码错乱、写入时被转义坏）；</li>
 *   <li>payload 里没有 {@code items} 数组，或者数组为空 —— 结构变了，不能当「没有待审项」放行；</li>
 *   <li>请求里要批的 clause 在快照里找不到，或快照项缺 risk —— 没有依据就写不出审计记录。</li>
 * </ol>
 *
 * <p>为什么必须 fail-closed：审批记录是审计凭证。一条 {@code risk}/{@code before_wording} 全 NULL
 * 的审批，在合规意义上等于没批过，但系统显示「已通过」——这个谎要到半年后审计才会被发现，
 * 而且无法补救（当时批的是哪条、什么风险，永远不知道）。审批失败用户立刻会重试；
 * 静默留空没有任何人会来问。这是数据诚信问题，不是体验问题。
 *
 * <p>映射为 HTTP 422 {@code APPROVAL_PAYLOAD_INVALID}。不能复用 409：409 的语义是
 * 「基线不匹配，刷新合同后重试」，而这里是「数据本身不合法，重试无用」——码混了，排障方向就错了。
 */
public class ApprovalPayloadInvalidException extends RuntimeException {

    private final String reviewId;
    private final String detail;

    public ApprovalPayloadInvalidException(String reviewId, String detail) {
        // 对外文案固定：具体是哪个 clause、JSON 第几行坏了，只进日志。
        super("审批数据不完整，无法生成审计记录，请重新发起审查");
        this.reviewId = reviewId;
        this.detail = detail;
    }

    public String getReviewId() {
        return reviewId;
    }

    public String getDetail() {
        return detail;
    }
}
