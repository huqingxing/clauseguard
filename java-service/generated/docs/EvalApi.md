# EvalApi

All URIs are relative to *http://localhost:8080*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**getEvalSummary**](EvalApi.md#getEvalSummary) | **GET** /api/v1/eval/summary | 评测看板汇总（token/成本/质量/增量收益） |



## getEvalSummary

> EvalSummary getEvalSummary()

评测看板汇总（token/成本/质量/增量收益）

Stage 12：聚合 review_event 中 run.completed 的 usage/quality/incremental 字段， 不建新表（数据本来就在事件流里）。

### Example

```java
// Import classes:
import com.clauseguard.api.ApiClient;
import com.clauseguard.api.ApiException;
import com.clauseguard.api.Configuration;
import com.clauseguard.api.models.*;
import com.clauseguard.api.client.EvalApi;

public class Example {
    public static void main(String[] args) {
        ApiClient defaultClient = Configuration.getDefaultApiClient();
        defaultClient.setBasePath("http://localhost:8080");

        EvalApi apiInstance = new EvalApi(defaultClient);
        try {
            EvalSummary result = apiInstance.getEvalSummary();
            System.out.println(result);
        } catch (ApiException e) {
            System.err.println("Exception when calling EvalApi#getEvalSummary");
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

[**EvalSummary**](EvalSummary.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: application/json


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | 汇总指标。 |  -  |

