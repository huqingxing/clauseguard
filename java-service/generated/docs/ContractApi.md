# ContractApi

All URIs are relative to *http://localhost:8080*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getContract**](ContractApi.md#getContract) | **GET** /api/v1/contracts/{id} | 合同详情（当前版本 + 最近审查） |
| [**getContractContent**](ContractApi.md#getContractContent) | **GET** /api/v1/contracts/{id}/content | 下载合同原文（当前版本二进制） |
| [**getContractDiff**](ContractApi.md#getContractDiff) | **GET** /api/v1/contracts/{id}/diff | 版本间条款变更对比（changed/added/removed） |
| [**listContracts**](ContractApi.md#listContracts) | **GET** /api/v1/contracts | 合同列表（含最近一次审查状态） |
| [**uploadContract**](ContractApi.md#uploadContract) | **POST** /api/v1/contracts | 上传合同（PDF/DOCX） |



## getContract

> ContractDetail getContract(id)

合同详情（当前版本 + 最近审查）

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ContractApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ContractApi apiInstance = new ContractApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        try {
            ContractDetail result = apiInstance.getContract(id);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ContractApi#getContract");
            System.err.println("Status code: " + e.getCode());
            System.err.println("Reason: " + e.getResponseBody());
            System.err.println("Response headers: " + e.getResponseHeaders());
            e.printStackTrace();
        }
    }
}
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **id** | **UUID**|  | |

### Return type

[**ContractDetail**](ContractDetail.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 合同详情。 |  -  |
| **404** | 资源不存在。 |  -  |


## getContractContent

> File getContractContent(id, version)

下载合同原文（当前版本二进制）

Stage 12：工作台左栏原文渲染的数据源（pdf.js / docx-preview 直接消费本流）， Python ingest 也经 Java 暴露的下载通道取文件（铁律 1：不直连业务库/存储）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ContractApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ContractApi apiInstance = new ContractApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        Integer version = 56; // Integer | 省略时返回当前版本。
        try {
            File result = apiInstance.getContractContent(id, version);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ContractApi#getContractContent");
            System.err.println("Status code: " + e.getCode());
            System.err.println("Reason: " + e.getResponseBody());
            System.err.println("Response headers: " + e.getResponseHeaders());
            e.printStackTrace();
        }
    }
}
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **id** | **UUID**|  | |
| **version** | **Integer**| 省略时返回当前版本。 | [optional] |

### Return type

[**File**](File.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document, application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 合同二进制（Content-Type 按文件类型）。 |  -  |
| **404** | 资源不存在。 |  -  |


## getContractDiff

> ContractDiff getContractDiff(id, target)

版本间条款变更对比（changed/added/removed）

Stage 12：数据来自该合同最近一次 run.completed 的 incremental 账本 （Python diff 的单一事实源），本端点只做透传整形，不重新计算。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ContractApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ContractApi apiInstance = new ContractApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        Integer target = 56; // Integer | 目标版本；省略时取最近一次审查的版本。
        try {
            ContractDiff result = apiInstance.getContractDiff(id, target);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ContractApi#getContractDiff");
            System.err.println("Status code: " + e.getCode());
            System.err.println("Reason: " + e.getResponseBody());
            System.err.println("Response headers: " + e.getResponseHeaders());
            e.printStackTrace();
        }
    }
}
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **id** | **UUID**|  | |
| **target** | **Integer**| 目标版本；省略时取最近一次审查的版本。 | [optional] |

### Return type

[**ContractDiff**](ContractDiff.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 变更对比结果。 |  -  |
| **404** | 资源不存在。 |  -  |


## listContracts

> ListContracts200Response listContracts(keyword, limit)

合同列表（含最近一次审查状态）

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ContractApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ContractApi apiInstance = new ContractApi(defaultClient);
        String keyword = "keyword_example"; // String | 按文件名模糊过滤。
        Integer limit = 20; // Integer | 
        try {
            ListContracts200Response result = apiInstance.listContracts(keyword, limit);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ContractApi#listContracts");
            System.err.println("Status code: " + e.getCode());
            System.err.println("Reason: " + e.getResponseBody());
            System.err.println("Response headers: " + e.getResponseHeaders());
            e.printStackTrace();
        }
    }
}
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **keyword** | **String**| 按文件名模糊过滤。 | [optional] |
| **limit** | **Integer**|  | [optional] [default to 20] |

### Return type

[**ListContracts200Response**](ListContracts200Response.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 合同摘要列表。 |  -  |


## uploadContract

> ContractUploadResponse uploadContract(_file)

上传合同（PDF/DOCX）

Stage 12：multipart 上传，落 Java 侧存储后返回 contract_id 与版本号； 前端随后以该 contract_id 调 createReview 发起审查。上传即版本 1， 覆盖上传产生新版本（版本号 +1）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ContractApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ContractApi apiInstance = new ContractApi(defaultClient);
        File _file = new File("/path/to/file"); // File | 合同文件，仅接受 .pdf/.docx。
        try {
            ContractUploadResponse result = apiInstance.uploadContract(_file);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ContractApi#uploadContract");
            System.err.println("Status code: " + e.getCode());
            System.err.println("Reason: " + e.getResponseBody());
            System.err.println("Response headers: " + e.getResponseHeaders());
            e.printStackTrace();
        }
    }
}
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **_file** | **File**| 合同文件，仅接受 .pdf/.docx。 | |

### Return type

[**ContractUploadResponse**](ContractUploadResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: multipart/form-data
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **201** | 上传成功。 |  -  |
| **400** | 请求体校验失败。 |  -  |

