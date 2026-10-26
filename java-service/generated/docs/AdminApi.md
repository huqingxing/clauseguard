# AdminApi

All URIs are relative to *http://localhost:8080*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getAdminHealth**](AdminApi.md#getAdminHealth) | **GET** /api/v1/admin/health | 双栈健康聚合（Java + Python） |



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

