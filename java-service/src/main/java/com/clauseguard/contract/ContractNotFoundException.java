package com.clauseguard.contract;

/** Stage 12：合同或合同版本不存在。 */
public class ContractNotFoundException extends RuntimeException {
    public ContractNotFoundException(String contractId) {
        super("contract not found: " + contractId);
    }
}
