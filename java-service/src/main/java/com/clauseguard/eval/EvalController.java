package com.clauseguard.eval;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Stage 12：评测看板入口。聚合逻辑在 EvalService（不在 Controller 写业务，铁律）。
 *
 * <p>落在 /api/v1/admin/eval 下，由 SecurityConfig 的 hasRole("ADMIN") 统一保护——
 * 评测看板暴露成本结构与用量，属内部运营数据，不能对匿名用户开放。
 */
@RestController
@RequestMapping("/api/v1/admin/eval")
public class EvalController {

    private final EvalService evalService;

    public EvalController(EvalService evalService) {
        this.evalService = evalService;
    }

    @GetMapping("/summary")
    public com.clauseguard.api.model.EvalSummary summary() {
        return evalService.summary();
    }
}
