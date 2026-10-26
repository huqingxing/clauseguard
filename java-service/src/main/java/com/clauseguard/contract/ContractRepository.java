package com.clauseguard.contract;

import com.clauseguard.contract.domain.Contract;
import com.clauseguard.contract.domain.Contract.ContractId;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface ContractRepository extends JpaRepository<Contract, ContractId> {

    List<Contract> findByIdOrderByVersionDesc(String id);

    Optional<Contract> findByIdAndVersion(String id, int version);

    /** 当前版本 = 该合同的最大版本号；上传链路据此决定新版本号。 */
    Optional<Contract> findFirstByIdOrderByVersionDesc(String id);

    List<Contract> findByFilenameContainingIgnoreCaseOrderByCreatedAtDesc(String filename);
}
