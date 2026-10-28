package com.clauseguard.review;

import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.SimpleTransactionStatus;

/**
 * 测试用的空事务管理器。
 *
 * <p>为什么需要：ReviewService 要求「事件行落库 + review.last_event_at 刷新」在同一个事务内
 * （阶段 13 写入路径），这是为了防止只有一半落库、让阶段 14 的超时回收读到陈旧时间戳。
 * 单元测试与 WebMvcTest 切片都没有真实数据源和事务管理器，这里给一个把回调就地跑完的空实现——
 * 目的是让写入路径的每一行都被真正执行到，而不是把事务边界从测试里绕开。
 */
final class NoopTransactionManager implements PlatformTransactionManager {

    static PlatformTransactionManager create() {
        return new NoopTransactionManager();
    }

    private NoopTransactionManager() {
    }

    @Override
    public TransactionStatus getTransaction(TransactionDefinition definition) {
        return new SimpleTransactionStatus();
    }

    @Override
    public void commit(TransactionStatus status) {
        // 无真实资源可提交：回调内的写入由被测的 mock repository 承接
    }

    @Override
    public void rollback(TransactionStatus status) {
        // 同上；失败时至少要保证不抛出二次异常掩盖真实原因
    }
}
