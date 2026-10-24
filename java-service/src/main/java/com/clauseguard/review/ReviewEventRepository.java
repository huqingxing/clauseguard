package com.clauseguard.review;

import com.clauseguard.review.domain.ReviewEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface ReviewEventRepository extends JpaRepository<ReviewEvent, Long> {
}
