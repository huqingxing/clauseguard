package com.clauseguard.api.client;

import com.clauseguard.api.ApiClient;
import com.clauseguard.api.BaseApi;

import com.clauseguard.api.model.ContractDetail;
import com.clauseguard.api.model.ContractDiff;
import com.clauseguard.api.model.ContractUploadResponse;
import com.clauseguard.api.model.Error;
import java.io.File;
import com.clauseguard.api.model.ListContracts200Response;
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
public class ContractApi extends BaseApi {

    public ContractApi() {
        super(new ApiClient());
    }

    public ContractApi(ApiClient apiClient) {
        super(apiClient);
    }

    /**
     * 合同详情（当前版本 + 最近审查）
     * 
     * <p><b>200</b> - 合同详情。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return ContractDetail
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ContractDetail getContract(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        return getContractWithHttpInfo(id).getBody();
    }

    /**
     * 合同详情（当前版本 + 最近审查）
     * 
     * <p><b>200</b> - 合同详情。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @return ResponseEntity&lt;ContractDetail&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ContractDetail> getContractWithHttpInfo(@jakarta.annotation.Nonnull UUID id) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling getContract");
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

        ParameterizedTypeReference<ContractDetail> localReturnType = new ParameterizedTypeReference<ContractDetail>() {};
        return apiClient.invokeAPI("/api/v1/contracts/{id}", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 下载合同原文（当前版本二进制）
     * Stage 12：工作台左栏原文渲染的数据源（pdf.js / docx-preview 直接消费本流）， Python ingest 也经 Java 暴露的下载通道取文件（铁律 1：不直连业务库/存储）。
     * <p><b>200</b> - 合同二进制（Content-Type 按文件类型）。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param version 省略时返回当前版本。 (optional)
     * @return File
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public File getContractContent(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nullable Integer version) throws RestClientException {
        return getContractContentWithHttpInfo(id, version).getBody();
    }

    /**
     * 下载合同原文（当前版本二进制）
     * Stage 12：工作台左栏原文渲染的数据源（pdf.js / docx-preview 直接消费本流）， Python ingest 也经 Java 暴露的下载通道取文件（铁律 1：不直连业务库/存储）。
     * <p><b>200</b> - 合同二进制（Content-Type 按文件类型）。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param version 省略时返回当前版本。 (optional)
     * @return ResponseEntity&lt;File&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<File> getContractContentWithHttpInfo(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nullable Integer version) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling getContractContent");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "version", version));
        

        final String[] localVarAccepts = { 
            "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<File> localReturnType = new ParameterizedTypeReference<File>() {};
        return apiClient.invokeAPI("/api/v1/contracts/{id}/content", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 版本间条款变更对比（changed/added/removed）
     * Stage 12：数据来自该合同最近一次 run.completed 的 incremental 账本 （Python diff 的单一事实源），本端点只做透传整形，不重新计算。
     * <p><b>200</b> - 变更对比结果。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param target 目标版本；省略时取最近一次审查的版本。 (optional)
     * @return ContractDiff
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ContractDiff getContractDiff(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nullable Integer target) throws RestClientException {
        return getContractDiffWithHttpInfo(id, target).getBody();
    }

    /**
     * 版本间条款变更对比（changed/added/removed）
     * Stage 12：数据来自该合同最近一次 run.completed 的 incremental 账本 （Python diff 的单一事实源），本端点只做透传整形，不重新计算。
     * <p><b>200</b> - 变更对比结果。
     * <p><b>404</b> - 资源不存在。
     * @param id  (required)
     * @param target 目标版本；省略时取最近一次审查的版本。 (optional)
     * @return ResponseEntity&lt;ContractDiff&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ContractDiff> getContractDiffWithHttpInfo(@jakarta.annotation.Nonnull UUID id, @jakarta.annotation.Nullable Integer target) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter 'id' is set
        if (id == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter 'id' when calling getContractDiff");
        }
        
        // create path and map variables
        final Map<String, Object> uriVariables = new HashMap<String, Object>();
        uriVariables.put("id", id);

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "target", target));
        

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<ContractDiff> localReturnType = new ParameterizedTypeReference<ContractDiff>() {};
        return apiClient.invokeAPI("/api/v1/contracts/{id}/diff", HttpMethod.GET, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 合同列表（含最近一次审查状态）
     * 
     * <p><b>200</b> - 合同摘要列表。
     * @param keyword 按文件名模糊过滤。 (optional)
     * @param limit  (optional, default to 20)
     * @return ListContracts200Response
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ListContracts200Response listContracts(@jakarta.annotation.Nullable String keyword, @jakarta.annotation.Nullable Integer limit) throws RestClientException {
        return listContractsWithHttpInfo(keyword, limit).getBody();
    }

    /**
     * 合同列表（含最近一次审查状态）
     * 
     * <p><b>200</b> - 合同摘要列表。
     * @param keyword 按文件名模糊过滤。 (optional)
     * @param limit  (optional, default to 20)
     * @return ResponseEntity&lt;ListContracts200Response&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ListContracts200Response> listContractsWithHttpInfo(@jakarta.annotation.Nullable String keyword, @jakarta.annotation.Nullable Integer limit) throws RestClientException {
        Object localVarPostBody = null;
        

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "keyword", keyword));
        localVarQueryParams.putAll(apiClient.parameterToMultiValueMap(null, "limit", limit));
        

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = {  };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<ListContracts200Response> localReturnType = new ParameterizedTypeReference<ListContracts200Response>() {};
        return apiClient.invokeAPI("/api/v1/contracts", HttpMethod.GET, Collections.<String, Object>emptyMap(), localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
    }
    /**
     * 上传合同（PDF/DOCX）
     * Stage 12：multipart 上传，落 Java 侧存储后返回 contract_id 与版本号； 前端随后以该 contract_id 调 createReview 发起审查。上传即版本 1， 覆盖上传产生新版本（版本号 +1）。
     * <p><b>201</b> - 上传成功。
     * <p><b>400</b> - 请求体校验失败。
     * @param _file 合同文件，仅接受 .pdf/.docx。 (required)
     * @return ContractUploadResponse
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ContractUploadResponse uploadContract(@jakarta.annotation.Nonnull File _file) throws RestClientException {
        return uploadContractWithHttpInfo(_file).getBody();
    }

    /**
     * 上传合同（PDF/DOCX）
     * Stage 12：multipart 上传，落 Java 侧存储后返回 contract_id 与版本号； 前端随后以该 contract_id 调 createReview 发起审查。上传即版本 1， 覆盖上传产生新版本（版本号 +1）。
     * <p><b>201</b> - 上传成功。
     * <p><b>400</b> - 请求体校验失败。
     * @param _file 合同文件，仅接受 .pdf/.docx。 (required)
     * @return ResponseEntity&lt;ContractUploadResponse&gt;
     * @throws RestClientException if an error occurs while attempting to invoke the API
     */
    public ResponseEntity<ContractUploadResponse> uploadContractWithHttpInfo(@jakarta.annotation.Nonnull File _file) throws RestClientException {
        Object localVarPostBody = null;
        
        // verify the required parameter '_file' is set
        if (_file == null) {
            throw new HttpClientErrorException(HttpStatus.BAD_REQUEST, "Missing the required parameter '_file' when calling uploadContract");
        }
        

        final MultiValueMap<String, String> localVarQueryParams = new LinkedMultiValueMap<String, String>();
        final HttpHeaders localVarHeaderParams = new HttpHeaders();
        final MultiValueMap<String, String> localVarCookieParams = new LinkedMultiValueMap<String, String>();
        final MultiValueMap<String, Object> localVarFormParams = new LinkedMultiValueMap<String, Object>();

        if (_file != null)
            localVarFormParams.add("file", new FileSystemResource(_file));

        final String[] localVarAccepts = { 
            "application/json"
         };
        final List<MediaType> localVarAccept = apiClient.selectHeaderAccept(localVarAccepts);
        final String[] localVarContentTypes = { 
            "multipart/form-data"
         };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        ParameterizedTypeReference<ContractUploadResponse> localReturnType = new ParameterizedTypeReference<ContractUploadResponse>() {};
        return apiClient.invokeAPI("/api/v1/contracts", HttpMethod.POST, Collections.<String, Object>emptyMap(), localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, localReturnType);
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
        final String[] localVarContentTypes = { 
            "multipart/form-data"
         };
        final MediaType localVarContentType = apiClient.selectHeaderContentType(localVarContentTypes);

        String[] localVarAuthNames = new String[] {  };

        return apiClient.invokeAPI(localVarPath, method, uriVariables, localVarQueryParams, localVarPostBody, localVarHeaderParams, localVarCookieParams, localVarFormParams, localVarAccept, localVarContentType, localVarAuthNames, returnType);
    }
}
