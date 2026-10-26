package com.clauseguard.api.client;

import com.clauseguard.api.ApiClient;
import com.clauseguard.api.BaseApi;

import com.clauseguard.api.model.AdminHealth;

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
