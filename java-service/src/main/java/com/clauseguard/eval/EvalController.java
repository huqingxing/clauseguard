package com.clauseguard.eval;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Stage 12：评测看板入口。聚合逻辑在 EvalService（不在 Controller 写业务，铁律）。
 */
@RestController
@RequestMapping("/api/v1/eval")
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
