package com.clauseguard.api.client;

import com.clauseguard.api.ApiClient;
import com.clauseguard.api.BaseApi;

import com.clauseguard.api.model.AdminHealth;
import com.clauseguard.api.model.ApprovalQueuePage;
import com.clauseguard.api.model.Error;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Collectors;

import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

@jakarta.annotation.Generated(value = "org.openapitools.codegen.languages.JavaClientCodegen", comments = "Generator version: 7.26.0")
public class AdminApi extends BaseApi {

    public AdminApi() {
        super(new ApiClient());
    }

    public AdminApi(ApiClient apiClient) {
        super(apiClient);
    }

    /**
     * 双栈健康聚合（Java + Python）
     * Stage 12：前端不直连 Python——由 Java 代理探测 Python /healthz 后聚合返回， 一屏看全双栈与依赖（MySQL/Redis/Postgres/MinIO）状态。
     * <p><b>200</b> - 聚合健康状态（任一依赖 down 也返回 200，由 details 表达）。
     * @return AdminHealth
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public AdminHealth getAdminHealth() throws RestClientException {
        return getAdminHealthWithHttpInfo().getBody();
    }

    /**
     * 双栈健康聚合（Java + Python）
     * Stage 12：前端不直连 Python——由 Java 代理探测 Python /healthz 后聚合返回， 一屏看全双栈与依赖（MySQL/Redis/Postgres/MinIO）状态。
     * <p><b>200</b> - 聚合健康状态（任一依赖 down 也返回 200，由 details 表达）。
     * @return ResponseEntity&lt;AdminHealth&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<AdminHealth> getAdminHealthWithHttpInfo() throws RestClientException {
        Object localVarPostBody = null;
        

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<AdminHealth> localReturnType = new ParameterizedTypeReference<AdminHealth>() {};
        return apiClient.invokeAPI("/api/v1/admin/health", HttpMethod.GET, Collections.<String, Object>emptyMap(), localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 待审批队列（审核者的待办列表）
     * Stage 5：审批归位到管理端后，审核者需要一个「我该批哪些」的入口。 数据全部来自既有 review / contract / review_event 三张表，不建队列表： 待审是状态而不是实体，一旦单独存表就会与 review.status 漂移。 status 只支持等待审批；不接受其它值（返回 400），避免静默返回空列表让人以为没活儿。
     * <p><b>200</b> - 按等待时长升序（最久的在最前）。
     * <p><b>400</b> - 请求体校验失败。
     * @param status 待审状态。对外名 WAITING_APPROVAL 与内部枚举 AWAITING_APPROVAL 都接受 （历史与契约用词不同，两个都认比逼调用方猜一个更稳），默认等待审批。 (optional, default to WAITING_APPROVAL)
     * @param page  (optional, default to 0)
     * @param size  (optional, default to 20)
     * @return ApprovalQueuePage
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ApprovalQueuePage listAdminApprovals(@jakarta.annotation.Nullable String status, @jakarta.annotation.Nullable Integer page, @jakarta.annotation.Nullable Integer size) throws RestClientException {
        return listAdminApprovalsWithHttpInfo(status, page, size).getBody();
    }

    /**
     * 待审批队列（审核者的待办列表）
     * Stage 5：审批归位到管理端后，审核者需要一个「我该批哪些」的入口。 数据全部来自既有 review / contract / review_event 三张表，不建队列表： 待审是状态而不是实体，一旦单独存表就会与 review.status 漂移。 status 只支持等待审批；不接受其它值（返回 400），避免静默返回空列表让人以为没活儿。
     * <p><b>200</b> - 按等待时长升序（最久的在最前）。
     * <p><b>400</b> - 请求体校验失败。
     * @param status 待审状态。对外名 WAITING_APPROVAL 与内部枚举 AWAITING_APPROVAL 都接受 （历史与契约用词不同，两个都认比逼调用方猜一个更稳），默认等待审批。 (optional, default to WAITING_APPROVAL)
     * @param page  (optional, default to 0)
     * @param size  (optional, default to 20)
     * @return ResponseEntity&lt;ApprovalQueuePage&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ApprovalQueuePage> listAdminApprovalsWithHttpInfo(@jakarta.annotation.Nullable String status, @jakarta.annotation.Nullable Integer page, @jakarta.annotation.Nullable Integer size) throws RestClientException {
        Object localVarPostBody = null;
        

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "status", status));
        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "page", page));
        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "size", size));
        

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<ApprovalQueuePage> localReturnType = new ParameterizedTypeReference<ApprovalQueuePage>() {};
        return apiClient.invokeAPI("/api/v1/admin/approvals", HttpMethod.GET, Collections.<String, Object>emptyMap(), localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }

    @Override
    public <T> ResponseEntity<T> invokeAPI(String url, HttpMethod method, Object request, ParameterizedTypeReference<T> returnType) throws RestClientException {
        String localVarPath = url.replace(apiClient.getBasePath(), "");
        Object localVarPostBody = request;

        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        return apiClient.invokeAPI(localVarPath, method, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, returnType);
    }
}
