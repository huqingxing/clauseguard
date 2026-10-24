package com.clauseguard.review;

/**
 * 审查任务不存在。映射到 HTTP 404。
 */
public class ReviewNotFoundException extends RuntimeException {

    public ReviewNotFoundException(String reviewId) {
        super("review not found: " + reviewId);
    }
}
