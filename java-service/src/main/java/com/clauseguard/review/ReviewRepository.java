package com.clauseguard.review;

import com.clauseguard.review.domain.Review;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface ReviewRepository extends JpaRepository<Review, String> {

    Optional<Review> findFirstByContractIdOrderByCreatedAtDesc(String contractId);

    Optional<Review> findFirstByContractIdAndVersionOrderByCreatedAtDesc(String contractId, int version);
}
