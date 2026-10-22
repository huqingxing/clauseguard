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
     * 高风险条款人工审批决定
     * 对单条高风险条款给出 approve / reject / edit 决定，edit 需附带 edited_text。
     * <p><b>200</b> - 决定已记录。
     * <p><b>400</b> - 请求体校验失败。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param approveRequest  (required)
     * @return ApproveResponse
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ApproveResponse approveReview(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nonnull ApproveRequest approveRequest) throws RestClientException {
        return approveReviewWithHttpInfo(id, approveRequest).getBody();
    }

    /**
     * 高风险条款人工审批决定
     * 对单条高风险条款给出 approve / reject / edit 决定，edit 需附带 edited_text。
     * <p><b>200</b> - 决定已记录。
     * <p><b>400</b> - 请求体校验失败。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param approveRequest  (required)
     * @return ResponseEntity&lt;ApproveResponse&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ApproveResponse> approveReviewWithHttpInfo(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nonnull ApproveRequest approveRequest) throws RestClientException {
        Object localVarPostBody = approveRequest;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling approveReview");
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
     * 针对已上传的合同发起一次审查运行，返回 review_id 与 SSE 订阅地址。
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
     * 针对已上传的合同发起一次审查运行，返回 review_id 与 SSE 订阅地址。
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
