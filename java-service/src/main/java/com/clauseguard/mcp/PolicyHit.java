package com.clauseguard.mcp;

/**
 * 制度检索命中。字段：id / title / text / score。
 */
public record PolicyHit(String id, String title, String text, double score) {
}
