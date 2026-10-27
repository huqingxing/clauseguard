package com.clauseguard.api.client;

import com.clauseguard.api.ApiClient;
import com.clauseguard.api.BaseApi;

import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.Error;
import com.clauseguard.api.model.FindingsPage;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.api.model.ReviewResult;
import java.util.UUID;

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
public class ReviewApi extends BaseApi {

    public ReviewApi() {
        super(new ApiClient());
    }

    public ReviewApi(ApiClient apiClient) {
        super(apiClient);
    }

    /**
     * 高风险条款人工审批决定（数组）
     * 审批是图的一部分：本端点校验 contract_version/content_hash（与中断时基线比对， 不一致返回 409 CONTRACT_CHANGED），落库每条审批记录（含操作人、时间、前后措辞）， 再转发到 Python resume 续跑。请求体为审批结果数组（每条对应一条待审高风险条款）。
     * <p><b>200</b> - 决定已记录，运行已恢复。
     * <p><b>400</b> - 请求体校验失败。
     * <p><b>404</b> - 资源不存在。
     * <p><b>409</b> - 合同版本/内容哈希与中断时基线不一致（合同已被修改），审批被拒，需重拉合同重试。
     * @param id  (required)
     * @param xOperator 审批操作人，写入 approval 表用于审计留痕。 非 ASCII 姓名须按 UTF-8 百分号编码（HTTP 头只能承载 ISO-8859-1， 浏览器对含中文的头会在发出前直接抛错），Java 侧解码后入库。 (required)
     * @param approveRequest  (required)
     * @return ApproveResponse
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ApproveResponse approveReview(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nonnull String xOperator, @jakarta.annotation.Nonnull List<ApproveRequest> approveRequest) throws RestClientException {
        return approveReviewWithHttpInfo(id, xOperator, approveRequest).getBody();
    }

    /**
     * 高风险条款人工审批决定（数组）
     * 审批是图的一部分：本端点校验 contract_version/content_hash（与中断时基线比对， 不一致返回 409 CONTRACT_CHANGED），落库每条审批记录（含操作人、时间、前后措辞）， 再转发到 Python resume 续跑。请求体为审批结果数组（每条对应一条待审高风险条款）。
     * <p><b>200</b> - 决定已记录，运行已恢复。
     * <p><b>400</b> - 请求体校验失败。
     * <p><b>404</b> - 资源不存在。
     * <p><b>409</b> - 合同版本/内容哈希与中断时基线不一致（合同已被修改），审批被拒，需重拉合同重试。
     * @param id  (required)
     * @param xOperator 审批操作人，写入 approval 表用于审计留痕。 非 ASCII 姓名须按 UTF-8 百分号编码（HTTP 头只能承载 ISO-8859-1， 浏览器对含中文的头会在发出前直接抛错），Java 侧解码后入库。 (required)
     * @param approveRequest  (required)
     * @return ResponseEntity&lt;ApproveResponse&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ApproveResponse> approveReviewWithHttpInfo(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nonnull String xOperator, @jakarta.annotation.Nonnull List<ApproveRequest> approveRequest) throws RestClientException {
        Object localVarPostBody = approveRequest;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling approveReview");
        }
        
        // verify the required parameter 'xOperator' is set
        if (xOperator == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'xOperator' when calling approveReview");
        }
        
        // verify the required parameter 'approveRequest' is set
        if (approveRequest == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'approveRequest' when calling approveReview");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        if (xOperator != null)
        localVarHeaderParams.add("X-Operator", apiClient.parameterToString(xOperator));

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = { 
            "application/json"
         };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<ApproveResponse> localReturnType = new ParameterizedTypeReference<ApproveResponse>() {};
        return apiClient.invokeAPI("/api/v1/reviews/{id}/approve", HttpMethod.POST, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 创建审查任务
     * 针对已上传的合同发起一次审查运行，返回 review_id 与 SSE 订阅地址。 Stage 11：mode&#x3D;incremental 时只重审相对 base_version 变更/新增的条款， 未变更条款的既有结论走缓存复用（base_version 省略时由编排层按 version-1 推断）。
     * <p><b>202</b> - 已受理，返回 review_id 与流地址。
     * <p><b>400</b> - 请求体校验失败。
     * @param reviewCreateRequest  (required)
     * @return ReviewCreateResponse
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ReviewCreateResponse createReview(@jakarta.annotation.Nonnull ReviewCreateRequest reviewCreateRequest) throws RestClientException {
        return createReviewWithHttpInfo(reviewCreateRequest).getBody();
    }

    /**
     * 创建审查任务
     * 针对已上传的合同发起一次审查运行，返回 review_id 与 SSE 订阅地址。 Stage 11：mode&#x3D;incremental 时只重审相对 base_version 变更/新增的条款， 未变更条款的既有结论走缓存复用（base_version 省略时由编排层按 version-1 推断）。
     * <p><b>202</b> - 已受理，返回 review_id 与流地址。
     * <p><b>400</b> - 请求体校验失败。
     * @param reviewCreateRequest  (required)
     * @return ResponseEntity&lt;ReviewCreateResponse&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ReviewCreateResponse> createReviewWithHttpInfo(@jakarta.annotation.Nonnull ReviewCreateRequest reviewCreateRequest) throws RestClientException {
        Object localVarPostBody = reviewCreateRequest;
        
        // verify the required parameter 'reviewCreateRequest' is set
        if (reviewCreateRequest == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'reviewCreateRequest' when calling createReview");
        }
        

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = { 
            "application/json"
         };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<ReviewCreateResponse> localReturnType = new ParameterizedTypeReference<ReviewCreateResponse>() {};
        return apiClient.invokeAPI("/api/v1/reviews", HttpMethod.POST, Collections.<String, Object>emptyMap(), localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 查询审查结果
     * 返回审查状态与汇总后的发现列表（完整列表，不分页）。
     * <p><b>200</b> - 审查结果与发现列表。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return ReviewResult
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ReviewResult getReview(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        return getReviewWithHttpInfo(id).getBody();
    }

    /**
     * 查询审查结果
     * 返回审查状态与汇总后的发现列表（完整列表，不分页）。
     * <p><b>200</b> - 审查结果与发现列表。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return ResponseEntity&lt;ReviewResult&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ReviewResult> getReviewWithHttpInfo(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling getReview");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

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

        ParameterizedTypeReference<ReviewResult> localReturnType = new ParameterizedTypeReference<ReviewResult>() {};
        return apiClient.invokeAPI("/api/v1/reviews/{id}", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 获取批注回写产物（带批注的 Word）
     * Stage 12：前端只认 Java——本端点从 run.completed 事件的 artifact 字段解析预签名 URL 并 302 跳转，浏览器不直连 MinIO。未回写（占位/未完成）时返回 404。
     * <p><b>302</b> - 重定向到带签名的下载 URL（限时有效）。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public void getReviewArtifact(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        getReviewArtifactWithHttpInfo(id);
    }

    /**
     * 获取批注回写产物（带批注的 Word）
     * Stage 12：前端只认 Java——本端点从 run.completed 事件的 artifact 字段解析预签名 URL 并 302 跳转，浏览器不直连 MinIO。未回写（占位/未完成）时返回 404。
     * <p><b>302</b> - 重定向到带签名的下载 URL（限时有效）。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return ResponseEntity&lt;Void&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<Void> getReviewArtifactWithHttpInfo(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling getReviewArtifact");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

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

        ParameterizedTypeReference<Void> localReturnType = new ParameterizedTypeReference<Void>() {};
        return apiClient.invokeAPI("/api/v1/reviews/{id}/artifact", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 分页查询审查发现
     * 
     * <p><b>200</b> - 分页发现列表。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param page  (optional, default to 0)
     * @param size  (optional, default to 20)
     * @return FindingsPage
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public FindingsPage listFindings(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nullable Integer page, @jakarta.annotation.Nullable Integer size) throws RestClientException {
        return listFindingsWithHttpInfo(id, page, size).getBody();
    }

    /**
     * 分页查询审查发现
     * 
     * <p><b>200</b> - 分页发现列表。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param page  (optional, default to 0)
     * @param size  (optional, default to 20)
     * @return ResponseEntity&lt;FindingsPage&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<FindingsPage> listFindingsWithHttpInfo(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nullable Integer page, @jakarta.annotation.Nullable Integer size) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling listFindings");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "page", page));
        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "size", size));
        

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<FindingsPage> localReturnType = new ParameterizedTypeReference<FindingsPage>() {};
        return apiClient.invokeAPI("/api/v1/reviews/{id}/findings", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 订阅该审查运行的 SSE 事件流
     * text/event-stream。每个 SSE data 行是一个 JSON 对象，结构严格符合 contracts/events.schema.json（公共字段 seq/ts/type + 各事件扁平负载）。
     * <p><b>200</b> - SSE 事件流。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return String
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public String streamReview(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        return streamReviewWithHttpInfo(id).getBody();
    }

    /**
     * 订阅该审查运行的 SSE 事件流
     * text/event-stream。每个 SSE data 行是一个 JSON 对象，结构严格符合 contracts/events.schema.json（公共字段 seq/ts/type + 各事件扁平负载）。
     * <p><b>200</b> - SSE 事件流。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return ResponseEntity&lt;String&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<String> streamReviewWithHttpInfo(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling streamReview");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        final String[] localVarAccepts = { 
            "text/event-stream", "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<String> localReturnType = new ParameterizedTypeReference<String>() {};
        return apiClient.invokeAPI("/api/v1/reviews/{id}/stream", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
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
            "text/event-stream", "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        return apiClient.invokeAPI(localVarPath, method, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, returnType);
    }
}
