# ReviewApi

All URIs are relative to *http://localhost:8080*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**approveReview**](ReviewApi.md#approveReview) | **POST** /api/v1/reviews/{id}/approve | 高风险条款人工审批决定（数组） |
| [**createReview**](ReviewApi.md#createReview) | **POST** /api/v1/reviews | 创建审查任务 |
| [**getReview**](ReviewApi.md#getReview) | **GET** /api/v1/reviews/{id} | 查询审查结果 |
| [**getReviewArtifact**](ReviewApi.md#getReviewArtifact) | **GET** /api/v1/reviews/{id}/artifact | 获取批注回写产物（带批注的 Word） |
| [**listFindings**](ReviewApi.md#listFindings) | **GET** /api/v1/reviews/{id}/findings | 分页查询审查发现 |
| [**streamReview**](ReviewApi.md#streamReview) | **GET** /api/v1/reviews/{id}/stream | 订阅该审查运行的 SSE 事件流 |



## approveReview

> ApproveResponse approveReview(id, xOperator, approveRequest)

高风险条款人工审批决定（数组）

审批是图的一部分：本端点校验 contract_version/content_hash（与中断时基线比对， 不一致返回 409 CONTRACT_CHANGED），落库每条审批记录（含操作人、时间、前后措辞）， 再转发到 Python resume 续跑。请求体为审批结果数组（每条对应一条待审高风险条款）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ReviewApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ReviewApi apiInstance = new ReviewApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        String xOperator = "xOperator_example"; // String | 审批操作人，写入 approval 表用于审计留痕。
        List<ApproveRequest> approveRequest = Arrays.asList(); // List<ApproveRequest> | 
        try {
            ApproveResponse result = apiInstance.approveReview(id, xOperator, approveRequest);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ReviewApi#approveReview");
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
| **xOperator** | **String**| 审批操作人，写入 approval 表用于审计留痕。 | |
| **approveRequest** | [**List&lt;ApproveRequest&gt;**](ApproveRequest.md)|  | |

### Return type

[**ApproveResponse**](ApproveResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: application/json
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 决定已记录，运行已恢复。 |  -  |
| **400** | 请求体校验失败。 |  -  |
| **404** | 资源不存在。 |  -  |
| **409** | 合同版本/内容哈希与中断时基线不一致（合同已被修改），审批被拒，需重拉合同重试。 |  -  |


## createReview

> ReviewCreateResponse createReview(reviewCreateRequest)

创建审查任务

针对已上传的合同发起一次审查运行，返回 review_id 与 SSE 订阅地址。 Stage 11：mode&#x3D;incremental 时只重审相对 base_version 变更/新增的条款， 未变更条款的既有结论走缓存复用（base_version 省略时由编排层按 version-1 推断）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ReviewApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ReviewApi apiInstance = new ReviewApi(defaultClient);
        ReviewCreateRequest reviewCreateRequest = new ReviewCreateRequest(); // ReviewCreateRequest | 
        try {
            ReviewCreateResponse result = apiInstance.createReview(reviewCreateRequest);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ReviewApi#createReview");
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
| **reviewCreateRequest** | [**ReviewCreateRequest**](ReviewCreateRequest.md)|  | |

### Return type

[**ReviewCreateResponse**](ReviewCreateResponse.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: application/json
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **202** | 已受理，返回 review_id 与流地址。 |  -  |
| **400** | 请求体校验失败。 |  -  |


## getReview

> ReviewResult getReview(id)

查询审查结果

返回审查状态与汇总后的发现列表（完整列表，不分页）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ReviewApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ReviewApi apiInstance = new ReviewApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        try {
            ReviewResult result = apiInstance.getReview(id);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ReviewApi#getReview");
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

[**ReviewResult**](ReviewResult.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 审查结果与发现列表。 |  -  |
| **404** | 资源不存在。 |  -  |


## getReviewArtifact

> getReviewArtifact(id)

获取批注回写产物（带批注的 Word）

Stage 12：前端只认 Java——本端点从 run.completed 事件的 artifact 字段解析预签名 URL 并 302 跳转，浏览器不直连 MinIO。未回写（占位/未完成）时返回 404。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ReviewApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ReviewApi apiInstance = new ReviewApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        try {
            apiInstance.getReviewArtifact(id);
        } catch (ApiException e) {
            System.err.println("Exception when calling ReviewApi#getReviewArtifact");
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

null (empty response body)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **302** | 重定向到带签名的下载 URL（限时有效）。 |  -  |
| **404** | 资源不存在。 |  -  |


## listFindings

> FindingsPage listFindings(id, page, size)

分页查询审查发现

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ReviewApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ReviewApi apiInstance = new ReviewApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        Integer page = 0; // Integer | 
        Integer size = 20; // Integer | 
        try {
            FindingsPage result = apiInstance.listFindings(id, page, size);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ReviewApi#listFindings");
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
| **page** | **Integer**|  | [optional] [default to 0] |
| **size** | **Integer**|  | [optional] [default to 20] |

### Return type

[**FindingsPage**](FindingsPage.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 分页发现列表。 |  -  |
| **404** | 资源不存在。 |  -  |


## streamReview

> String streamReview(id)

订阅该审查运行的 SSE 事件流

text/event-stream。每个 SSE data 行是一个 JSON 对象，结构严格符合 contracts/events.schema.json（公共字段 seq/ts/type + 各事件扁平负载）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.ReviewApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        ReviewApi apiInstance = new ReviewApi(defaultClient);
        UUID id = UUID.randomUUID(); // UUID | 
        try {
            String result = apiInstance.streamReview(id);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling ReviewApi#streamReview");
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

**String**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: text/event-stream, application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | SSE 事件流。 |  -  |
| **404** | 资源不存在。 |  -  |

