# AdminApi

All URIs are relative to *http://localhost:8080*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getAdminHealth**](AdminApi.md#getAdminHealth) | **GET** /api/v1/admin/health | 双栈健康聚合（Java + Python） |
| [**listAdminApprovals**](AdminApi.md#listAdminApprovals) | **GET** /api/v1/admin/approvals | 待审批队列（审核者的待办列表） |



## getAdminHealth

> AdminHealth getAdminHealth()

双栈健康聚合（Java + Python）

Stage 12：前端不直连 Python——由 Java 代理探测 Python /healthz 后聚合返回， 一屏看全双栈与依赖（MySQL/Redis/Postgres/MinIO）状态。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.AdminApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        AdminApi apiInstance = new AdminApi(defaultClient);
        try {
            AdminHealth result = apiInstance.getAdminHealth();
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling AdminApi#getAdminHealth");
            System.err.println("Status code: " + e.getCode());
            System.err.println("Reason: " + e.getResponseBody());
            System.err.println("Response headers: " + e.getResponseHeaders());
            e.printStackTrace();
        }
    }
}
```

### Parameters

This endpoint does not need any parameter.

### Return type

[**AdminHealth**](AdminHealth.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 聚合健康状态（任一依赖 down 也返回 200，由 details 表达）。 |  -  |


## listAdminApprovals

> ApprovalQueuePage listAdminApprovals(status, page, size)

待审批队列（审核者的待办列表）

Stage 5：审批归位到管理端后，审核者需要一个「我该批哪些」的入口。 数据全部来自既有 review / contract / review_event 三张表，不建队列表： 待审是状态而不是实体，一旦单独存表就会与 review.status 漂移。 status 只支持等待审批；不接受其它值（返回 400），避免静默返回空列表让人以为没活儿。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.AdminApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        AdminApi apiInstance = new AdminApi(defaultClient);
        String status = "WAITING_APPROVAL"; // String | 待审状态。对外名 WAITING_APPROVAL 与内部枚举 AWAITING_APPROVAL 都接受 （历史与契约用词不同，两个都认比逼调用方猜一个更稳），默认等待审批。
        Integer page = 0; // Integer | 
        Integer size = 20; // Integer | 
        try {
            ApprovalQueuePage result = apiInstance.listAdminApprovals(status, page, size);
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling AdminApi#listAdminApprovals");
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
| **status** | **String**| 待审状态。对外名 WAITING_APPROVAL 与内部枚举 AWAITING_APPROVAL 都接受 （历史与契约用词不同，两个都认比逼调用方猜一个更稳），默认等待审批。 | [optional] [default to WAITING_APPROVAL] |
| **page** | **Integer**|  | [optional] [default to 0] |
| **size** | **Integer**|  | [optional] [default to 20] |

### Return type

[**ApprovalQueuePage**](ApprovalQueuePage.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 按等待时长升序（最久的在最前）。 |  -  |
| **400** | 请求体校验失败。 |  -  |

