package com.clauseguard.admin;

import com.clauseguard.api.model.AdminHealth;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * AdminController web 切片测试：双栈健康聚合透传。Python 不可达由 Service 记 down
 * （本端点仍 200——单栈故障不掩盖另一栈状态）。
 */
@WebMvcTest(AdminController.class)
class AdminControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private AdminHealthService adminHealthService;

    @Test
    void healthReturnsBothStacks() throws Exception {
        when(adminHealthService.health()).thenReturn(new AdminHealth()
                .java(new com.clauseguard.api.model.AdminHealthJava()
                        .status(com.clauseguard.api.model.AdminHealthJava.StatusEnum.UP))
                .python(new com.clauseguard.api.model.AdminHealthPython()
                        .status(com.clauseguard.api.model.AdminHealthPython.StatusEnum.UP)
                        .detail(java.util.Map.of("status", "ok"))));

        mockMvc.perform(get("/api/v1/admin/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.java.status").value("up"))
                .andExpect(jsonPath("$.python.status").value("up"))
                .andExpect(jsonPath("$.python.detail.status").value("ok"));
    }

    @Test
    void healthMarksPythonDownWhenUnreachable() throws Exception {
        when(adminHealthService.health()).thenReturn(new AdminHealth()
                .java(new com.clauseguard.api.model.AdminHealthJava()
                        .status(com.clauseguard.api.model.AdminHealthJava.StatusEnum.UP))
                .python(new com.clauseguard.api.model.AdminHealthPython()
                        .status(com.clauseguard.api.model.AdminHealthPython.StatusEnum.DOWN)));

        mockMvc.perform(get("/api/v1/admin/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.python.status").value("down"));
    }
}
