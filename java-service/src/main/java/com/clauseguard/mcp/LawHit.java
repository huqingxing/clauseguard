package com.clauseguard.mcp;

/**
 * 法条检索命中。字段与 openapi/检索白名单契约一致：id / article / text / score。
 */
public record LawHit(String id, String article, String text, double score) {
}
